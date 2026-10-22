"""The async job API: POST /jobs (202), GET /jobs/{id}, idempotency, failures, and resumption.

The engine, the TEE and the zkML prover are the real components on a tiny linear model. Only
storage is a double, as in the other API tests.
"""

import asyncio
import base64
import hashlib
import time
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import fakeredis.aioredis as fakeredis
import pytest
import pytest_asyncio
from eth_account import Account
from eth_account.messages import encode_defunct
from httpx import ASGITransport, AsyncClient

from common.model_registry import ModelRegistry
from common.models import ProofArtifact
from inference.app import app
from inference.auth import Challenges
from inference.engine import InferenceEngine
from inference.jobs import INDEX_KEY, INPUT_KEY, JobService
from inference.tee_runtime import TEERuntime
from proof_generator.verifier import ProofVerifier
from proof_generator.zkml_prover import ZkMLProver
from proof_submitter.submitter import ProofSubmitter
from tests.onnx_fixtures import linear_model_bytes, tensor_bytes
from tests.property.test_model_registry_prop import MockStorageBackend

TASK_ID = "0x" + "ab" * 32
MODEL_ID = "job_model"
# Test-only keys. The worker of TASK_ID is WALLET; OTHER is a wallet that is not.
WALLET = Account.from_key("0x" + "11" * 32)
OTHER = Account.from_key("0x" + "22" * 32)


async def signed_body(
    input_data: bytes,
    model_id: str | None = MODEL_ID,
    model_cid: str | None = None,
    task_id: str = TASK_ID,
    account: Account = WALLET,
    signer: Account | None = None,
) -> dict[str, Any]:
    """A job body with a challenge from the service, signed by ``signer`` (default ``account``)."""
    challenge = await app.state.challenges.issue(task_id, account.address)
    signature = (signer or account).sign_message(encode_defunct(text=challenge["message"])).signature
    body: dict[str, Any] = {
        "task_id": task_id,
        "input_hash": hashlib.sha256(input_data).hexdigest(),
        "input_data": base64.b64encode(input_data).decode(),
        "authorization": {
            "wallet_address": account.address,
            "nonce": challenge["nonce"],
            "expires_at": challenge["expires_at"],
            "signature": "0x" + bytes(signature).hex(),
        },
    }
    if model_cid is not None:
        body["model_cid"] = model_cid
    else:
        body["model_id"] = model_id
    return body


async def read_headers(task_id: str = TASK_ID, account: Account = WALLET, signer: Account | None = None) -> dict[str, str]:
    """Headers that let one GET /jobs/{id} read a job of ``task_id``. Each read needs a new challenge."""
    challenge = await app.state.challenges.issue(task_id, account.address)
    signature = (signer or account).sign_message(encode_defunct(text=challenge["message"])).signature
    return {
        "X-Wallet-Address": account.address,
        "X-Challenge-Nonce": challenge["nonce"],
        "X-Challenge-Expires-At": str(challenge["expires_at"]),
        "X-Challenge-Signature": "0x" + bytes(signature).hex(),
    }


async def wait_for_status(client: AsyncClient, job_id: str, statuses: set[str], timeout: float = 20.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while True:
        response = await client.get(f"/jobs/{job_id}", headers=await read_headers())
        job = response.json()
        if job.get("status") in statuses:
            return job
        if time.monotonic() > deadline:
            raise AssertionError(f"job did not reach {statuses}: {job}")
        await asyncio.sleep(0.05)


@pytest_asyncio.fixture
async def jobs(tmp_path: Path) -> AsyncIterator[tuple[JobService, Any, fakeredis.FakeRedis]]:
    redis_client = fakeredis.FakeRedis(decode_responses=False)
    storage = MockStorageBackend()
    registry = ModelRegistry(redis_client, storage)
    tee = TEERuntime("sgx")
    tee.initialize()
    engine = InferenceEngine(registry, tee, redis_client, timeout_s=10.0)
    prover = ZkMLProver(registry, proving_key_store=str(tmp_path / "keys"), backend="development")
    model = linear_model_bytes()
    cid = await storage.upload(model)
    await registry.register(MODEL_ID, cid, model, architecture="linear")

    workers = {TASK_ID: WALLET.address}

    async def task_worker(task_id: str) -> str | None:
        return workers.get(task_id)

    service = JobService(redis_client, engine, tee, prover)
    state = app.state
    state.redis_client = redis_client
    state.storage_backend = storage
    state.model_registry = registry
    state.tee_runtime = tee
    state.inference_engine = engine
    state.zkml_prover = prover
    state.job_service = service
    state.challenges = Challenges(redis_client, task_worker)
    state.task_workers = workers

    yield service, engine, redis_client
    await service.shutdown()


@pytest.mark.asyncio
async def test_post_returns_202_and_the_completed_receipt_carries_attestation_and_proof(
    jobs: tuple[JobService, Any, fakeredis.FakeRedis],
) -> None:
    input_data = tensor_bytes(seed=3)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/jobs", json=await signed_body(input_data))
        assert response.status_code == 202
        accepted = response.json()
        assert accepted["created"] is True
        assert accepted["status"] in {"queued", "running", "completed"}
        assert response.headers["location"] == f"/jobs/{accepted['job_id']}"

        job = await wait_for_status(client, accepted["job_id"], {"completed", "failed"})

    assert job["status"] == "completed", job
    assert job["error"] is None
    receipt = job["receipt"]
    input_hash = hashlib.sha256(input_data).hexdigest()
    assert receipt["execution"]["input_hash"] == input_hash
    assert receipt["attestation"]["report"]["input_hash"] == input_hash
    assert receipt["attestation"]["report"]["output_hash"] == receipt["execution"]["output_hash"]
    assert receipt["attestation"]["signer_public_key"]
    assert receipt["zkml_proof"]["public_inputs"]["input_hash"] == input_hash
    assert receipt["zkml_proof"]["backend"] == "development-commitment"


@pytest.mark.asyncio
async def test_the_zkml_proof_in_a_receipt_verifies(jobs: tuple[JobService, Any, fakeredis.FakeRedis]) -> None:
    input_data = tensor_bytes(seed=5)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        accepted = (await client.post("/jobs", json=await signed_body(input_data))).json()
        job = await wait_for_status(client, accepted["job_id"], {"completed"})

    proof_payload = job["receipt"]["zkml_proof"]
    artifact = ProofArtifact(
        proof_bytes=base64.b64decode(proof_payload["proof_bytes"]),
        public_inputs=proof_payload["public_inputs"],
        verification_key_ref=proof_payload["verification_key_ref"],
        backend=proof_payload["backend"],
    )
    assert ProofVerifier().verify(artifact)


@pytest.mark.asyncio
async def test_the_same_task_and_input_is_one_job_that_runs_once(
    jobs: tuple[JobService, Any, fakeredis.FakeRedis],
) -> None:
    service, engine, redis_client = jobs
    runs = 0
    original_execute = engine.execute

    async def counting_execute(request: Any) -> Any:
        nonlocal runs
        runs += 1
        return await original_execute(request)

    engine.execute = counting_execute  # type: ignore[method-assign]
    input_data = tensor_bytes(seed=7)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = (await client.post("/jobs", json=await signed_body(input_data))).json()
        second_response = await client.post("/jobs", json=await signed_body(input_data))
        second = second_response.json()
        await wait_for_status(client, first["job_id"], {"completed"})

    assert second_response.status_code == 202
    assert second["job_id"] == first["job_id"]
    assert second["created"] is False
    assert runs == 1
    job_records = [key async for key in redis_client.scan_iter(match="jobs:job:*")]
    assert len(job_records) == 1


@pytest.mark.asyncio
async def test_the_same_task_and_input_for_another_model_is_a_conflict(
    jobs: tuple[JobService, Any, fakeredis.FakeRedis],
) -> None:
    input_data = tensor_bytes(seed=9)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = (await client.post("/jobs", json=await signed_body(input_data))).json()
        conflict = await client.post("/jobs", json=await signed_body(input_data, model_id="another_model"))

    assert conflict.status_code == 409
    assert conflict.json()["error"] == "JobConflict"
    assert first["model_id"] == MODEL_ID


@pytest.mark.asyncio
async def test_an_input_that_does_not_match_its_hash_is_rejected(jobs: tuple[JobService, Any, fakeredis.FakeRedis]) -> None:
    input_data = tensor_bytes(seed=11)
    payload = await signed_body(input_data)
    payload["input_hash"] = hashlib.sha256(b"something else").hexdigest()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/jobs", json=payload)

    assert response.status_code == 422
    assert response.json()["error"] == "InvalidJob"


@pytest.mark.asyncio
async def test_an_input_that_is_not_a_tensor_is_rejected(jobs: tuple[JobService, Any, fakeredis.FakeRedis]) -> None:
    not_a_tensor = b"not an npy tensor"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/jobs", json=await signed_body(not_a_tensor))

    assert response.status_code == 422
    assert "not an .npy tensor" in response.json()["detail"]


@pytest.mark.asyncio
async def test_a_malformed_task_id_or_hash_is_rejected_by_the_schema(jobs: tuple[JobService, Any, fakeredis.FakeRedis]) -> None:
    input_data = tensor_bytes(seed=13)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        bad_task = await client.post("/jobs", json=await signed_body(input_data, task_id="0x1234"))
        bad_base64 = await client.post("/jobs", json={**(await signed_body(input_data)), "input_data": "!!not base64!!"})

    assert bad_task.status_code == 422
    assert bad_base64.status_code == 422


@pytest.mark.asyncio
async def test_an_unknown_model_fails_the_job_with_an_error_code(jobs: tuple[JobService, Any, fakeredis.FakeRedis]) -> None:
    input_data = tensor_bytes(seed=15)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        accepted = (await client.post("/jobs", json=await signed_body(input_data, model_id="not_registered"))).json()
        job = await wait_for_status(client, accepted["job_id"], {"failed", "completed"})

    assert job["status"] == "failed"
    assert job["error"]["code"] == "model_not_found"
    assert job["receipt"] is None


@pytest.mark.asyncio
async def test_an_unknown_job_is_404(jobs: tuple[JobService, Any, fakeredis.FakeRedis]) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/jobs/" + "0" * 32)

    assert response.status_code == 404
    assert response.json()["error"] == "JobNotFound"


@pytest.mark.asyncio
async def test_the_input_is_dropped_once_the_job_finishes(jobs: tuple[JobService, Any, fakeredis.FakeRedis]) -> None:
    service, _engine, redis_client = jobs
    input_data = tensor_bytes(seed=17)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        accepted = (await client.post("/jobs", json=await signed_body(input_data))).json()
        await wait_for_status(client, accepted["job_id"], {"completed"})

    assert await redis_client.get(INPUT_KEY.format(accepted["job_id"])) is None


@pytest.mark.asyncio
async def test_unfinished_jobs_are_run_again_after_a_restart(jobs: tuple[JobService, Any, fakeredis.FakeRedis]) -> None:
    _service, engine, redis_client = jobs
    input_data = tensor_bytes(seed=19)
    task_id = TASK_ID
    input_hash = hashlib.sha256(input_data).hexdigest()

    # The first service accepts the job and stops before it runs, as on shutdown mid-queue.
    first = JobService(redis_client, engine, _service.tee, _service.prover)
    job, created = await first.submit(task_id, input_hash, MODEL_ID, input_data)
    await first.shutdown()
    assert created is True
    assert job["status"] == "queued"

    # A new service on the same Redis picks the job up and finishes it.
    restarted = JobService(redis_client, engine, _service.tee, _service.prover)
    await restarted.resume()
    deadline = time.monotonic() + 20
    while True:
        current = await restarted.get(job["job_id"])
        assert current is not None
        if current["status"] in {"completed", "failed"}:
            break
        assert time.monotonic() < deadline, current
        await asyncio.sleep(0.05)
    await restarted.shutdown()

    assert current["status"] == "completed", current
    assert await redis_client.get(INDEX_KEY.format(task_id, input_hash)) == job["job_id"].encode()


@pytest.mark.asyncio
async def test_a_job_that_outlives_its_budget_fails_as_job_timeout(
    jobs: tuple[JobService, Any, fakeredis.FakeRedis],
) -> None:
    _service, engine, redis_client = jobs

    async def stalled_execute(_request: Any) -> Any:
        await asyncio.sleep(30)

    engine.execute = stalled_execute  # type: ignore[method-assign]
    app.state.job_service = JobService(redis_client, engine, _service.tee, _service.prover, timeout_s=0.3)
    input_data = tensor_bytes(seed=21)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        accepted = (await client.post("/jobs", json=await signed_body(input_data))).json()
        running = await client.get(f"/jobs/{accepted['job_id']}", headers=await read_headers())
        job = await wait_for_status(client, accepted["job_id"], {"failed", "completed"})

    assert running.headers["retry-after"] == "5"
    assert job["status"] == "failed"
    assert job["error"]["code"] == "job_timeout"
    assert job["receipt"] is None


@pytest.mark.asyncio
async def test_the_post_reports_the_deadline_the_client_can_poll_until(
    jobs: tuple[JobService, Any, fakeredis.FakeRedis],
) -> None:
    input_data = tensor_bytes(seed=23)
    before = time.time()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        accepted = (await client.post("/jobs", json=await signed_body(input_data))).json()
        job = await wait_for_status(client, accepted["job_id"], {"completed", "failed"})
        finished = await client.get(f"/jobs/{accepted['job_id']}", headers=await read_headers())

    assert accepted["deadline"] >= before + 150
    assert job["deadline"] == accepted["deadline"]
    assert "retry-after" not in finished.headers


async def register_other_model(seed: int, model_id: str) -> str:
    storage = app.state.storage_backend
    binary = linear_model_bytes(seed)
    cid = await storage.upload(binary)
    await app.state.model_registry.register(model_id, cid, binary, architecture="linear")
    return cid


@pytest.mark.asyncio
async def test_a_job_can_name_its_model_by_cid(jobs: tuple[JobService, Any, fakeredis.FakeRedis]) -> None:
    cid = await register_other_model(8, "cid_model")
    input_data = tensor_bytes(seed=25)
    payload = await signed_body(input_data, model_cid=cid)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        accepted = (await client.post("/jobs", json=payload)).json()
        job = await wait_for_status(client, accepted["job_id"], {"completed", "failed"})

    assert job["status"] == "completed", job
    assert job["model_id"] == "cid_model"
    assert job["receipt"]["execution"]["model_cid"] == cid


@pytest.mark.asyncio
async def test_a_cid_that_is_no_longer_the_current_version_does_not_resolve(
    jobs: tuple[JobService, Any, fakeredis.FakeRedis],
) -> None:
    old_cid = await register_other_model(8, "versioned_model")
    await register_other_model(9, "versioned_model")

    assert await app.state.model_registry.get_by_cid(old_cid) is None


@pytest.mark.asyncio
async def test_an_unknown_cid_is_404_and_naming_no_model_or_two_is_422(
    jobs: tuple[JobService, Any, fakeredis.FakeRedis],
) -> None:
    input_data = tensor_bytes(seed=27)
    unknown = await signed_body(input_data, model_cid="QmUnknown")
    neither = {k: v for k, v in unknown.items() if k != "model_cid"}
    both = {**unknown, "model_id": MODEL_ID}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        missing = await client.post("/jobs", json=unknown)
        no_model = await client.post("/jobs", json=neither)
        two_models = await client.post("/jobs", json=both)

    assert missing.status_code == 404
    assert missing.json()["error"] == "ModelNotFound"
    assert no_model.status_code == 422
    assert two_models.status_code == 422


class FakeSubmitter:
    """Stands in for the proof submitter: records what it was given and returns a set outcome."""

    def __init__(self, tx_hash: str | None = "0xabc", submit_error: Exception | None = None) -> None:
        self.tx_hash = tx_hash
        self.submit_error = submit_error
        self.submitted: list[tuple[str, str]] = []

    async def submit_proof(self, task_id: str, _attestation: Any, _proof: Any, model_cid: str) -> None:
        if self.submit_error is not None:
            raise self.submit_error
        self.submitted.append((task_id, model_cid))

    async def flush(self) -> str | None:
        return self.tx_hash


async def run_one_job(service: JobService, seed: int) -> dict[str, Any]:
    app.state.job_service = service
    input_data = tensor_bytes(seed=seed)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        accepted = (await client.post("/jobs", json=await signed_body(input_data))).json()
        return await wait_for_status(client, accepted["job_id"], {"completed", "failed"})


@pytest.mark.asyncio
async def test_settlement_is_disabled_without_a_submitter(jobs: tuple[JobService, Any, fakeredis.FakeRedis]) -> None:
    service, engine, redis_client = jobs
    job = await run_one_job(JobService(redis_client, engine, service.tee, service.prover), seed=29)

    assert job["status"] == "completed"
    assert job["receipt"]["settlement"] == {"status": "disabled"}


@pytest.mark.asyncio
async def test_a_development_proof_is_not_settled_and_the_job_still_completes(
    jobs: tuple[JobService, Any, fakeredis.FakeRedis],
) -> None:
    service, engine, redis_client = jobs
    real = ProofSubmitter(SimpleNamespace(), SimpleNamespace(), redis_client)
    job = await run_one_job(JobService(redis_client, engine, service.tee, service.prover, submitter=real), seed=31)

    assert job["status"] == "completed"
    assert job["receipt"]["settlement"]["status"] == "not_submitted"
    assert "development-commitment" in job["receipt"]["settlement"]["reason"]


@pytest.mark.asyncio
async def test_an_accepted_proof_reports_its_transaction_hash(jobs: tuple[JobService, Any, fakeredis.FakeRedis]) -> None:
    service, engine, redis_client = jobs
    submitter = FakeSubmitter(tx_hash="0xfeed")
    job = await run_one_job(JobService(redis_client, engine, service.tee, service.prover, submitter=submitter), seed=33)

    assert job["receipt"]["settlement"] == {"status": "submitted", "tx_hash": "0xfeed"}
    assert submitter.submitted == [(TASK_ID, job["receipt"]["execution"]["model_cid"])]


@pytest.mark.asyncio
async def test_a_dead_lettered_batch_is_reported_and_a_failed_submission_does_not_fail_the_job(
    jobs: tuple[JobService, Any, fakeredis.FakeRedis],
) -> None:
    service, engine, redis_client = jobs
    dead_lettered = await run_one_job(
        JobService(redis_client, engine, service.tee, service.prover, submitter=FakeSubmitter(tx_hash=None)),
        seed=35,
    )
    failing = await run_one_job(
        JobService(
            redis_client,
            engine,
            service.tee,
            service.prover,
            submitter=FakeSubmitter(submit_error=RuntimeError("rpc unreachable")),
        ),
        seed=37,
    )

    assert dead_lettered["receipt"]["settlement"] == {"status": "dead_lettered"}
    assert failing["status"] == "completed"
    assert failing["receipt"]["settlement"]["status"] == "failed"
    assert "rpc unreachable" in failing["receipt"]["settlement"]["reason"]


@pytest.mark.asyncio
async def test_a_challenge_names_the_task_and_wallet_and_is_fresh_each_time(
    jobs: tuple[JobService, Any, fakeredis.FakeRedis],
) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.post("/challenges", json={"task_id": TASK_ID, "wallet_address": WALLET.address})
        second = await client.post("/challenges", json={"task_id": TASK_ID, "wallet_address": WALLET.address})

    assert first.status_code == 201
    assert TASK_ID in first.json()["message"]
    assert WALLET.address.lower() in first.json()["message"]
    assert first.json()["nonce"] != second.json()["nonce"]


@pytest.mark.asyncio
async def test_a_job_without_a_signed_challenge_is_rejected(jobs: tuple[JobService, Any, fakeredis.FakeRedis]) -> None:
    payload = await signed_body(tensor_bytes(seed=41))
    del payload["authorization"]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/jobs", json=payload)

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_a_signature_from_another_key_is_refused(jobs: tuple[JobService, Any, fakeredis.FakeRedis]) -> None:
    payload = await signed_body(tensor_bytes(seed=43), signer=OTHER)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/jobs", json=payload)

    assert response.status_code == 401
    assert response.json()["error"] == "bad_signature"


@pytest.mark.asyncio
async def test_a_wallet_that_is_not_the_task_worker_is_forbidden(jobs: tuple[JobService, Any, fakeredis.FakeRedis]) -> None:
    payload = await signed_body(tensor_bytes(seed=45), account=OTHER)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/jobs", json=payload)

    assert response.status_code == 403
    assert response.json()["error"] == "not_task_worker"


@pytest.mark.asyncio
async def test_a_challenge_is_good_for_one_request_only(jobs: tuple[JobService, Any, fakeredis.FakeRedis]) -> None:
    payload = await signed_body(tensor_bytes(seed=47))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.post("/jobs", json=payload)
        replay = await client.post("/jobs", json=payload)

    assert first.status_code == 202
    assert replay.status_code == 401
    assert replay.json()["error"] == "challenge_unknown"


@pytest.mark.asyncio
async def test_a_challenge_is_bound_to_the_task_it_was_issued_for(jobs: tuple[JobService, Any, fakeredis.FakeRedis]) -> None:
    payload = await signed_body(tensor_bytes(seed=49))
    payload["task_id"] = "0x" + "cd" * 32
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/jobs", json=payload)

    assert response.status_code == 401
    assert response.json()["error"] == "challenge_mismatch"


@pytest.mark.asyncio
async def test_an_expired_challenge_is_refused(jobs: tuple[JobService, Any, fakeredis.FakeRedis]) -> None:
    payload = await signed_body(tensor_bytes(seed=51))
    app.state.challenges.clock = lambda: time.time() + 10_000
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/jobs", json=payload)

    assert response.status_code == 401
    assert response.json()["error"] == "challenge_expired"


@pytest.mark.asyncio
async def test_no_job_is_accepted_without_a_chain_to_check_the_worker(
    jobs: tuple[JobService, Any, fakeredis.FakeRedis],
) -> None:
    payload = await signed_body(tensor_bytes(seed=53))
    app.state.challenges.lookup = None
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/jobs", json=payload)

    assert response.status_code == 503
    assert response.json()["error"] == "auth_unavailable"


async def posted_job(client: AsyncClient, seed: int) -> str:
    accepted = (await client.post("/jobs", json=await signed_body(tensor_bytes(seed=seed)))).json()
    return str(accepted["job_id"])


@pytest.mark.asyncio
async def test_reading_a_job_without_a_signed_challenge_is_refused(jobs: tuple[JobService, Any, fakeredis.FakeRedis]) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        job_id = await posted_job(client, seed=55)
        response = await client.get(f"/jobs/{job_id}")

    assert response.status_code == 401
    assert response.json()["error"] == "challenge_required"


@pytest.mark.asyncio
async def test_reading_with_a_signature_from_another_key_is_refused(
    jobs: tuple[JobService, Any, fakeredis.FakeRedis],
) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        job_id = await posted_job(client, seed=57)
        response = await client.get(f"/jobs/{job_id}", headers=await read_headers(signer=OTHER))

    assert response.status_code == 401
    assert response.json()["error"] == "bad_signature"


@pytest.mark.asyncio
async def test_reading_by_a_wallet_that_is_not_the_task_worker_is_forbidden(
    jobs: tuple[JobService, Any, fakeredis.FakeRedis],
) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        job_id = await posted_job(client, seed=59)
        response = await client.get(f"/jobs/{job_id}", headers=await read_headers(account=OTHER))

    assert response.status_code == 403
    assert response.json()["error"] == "not_task_worker"


@pytest.mark.asyncio
async def test_a_read_challenge_is_good_for_one_read(jobs: tuple[JobService, Any, fakeredis.FakeRedis]) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        job_id = await posted_job(client, seed=61)
        headers = await read_headers()
        first = await client.get(f"/jobs/{job_id}", headers=headers)
        replay = await client.get(f"/jobs/{job_id}", headers=headers)

    assert first.status_code == 200
    assert replay.status_code == 401
    assert replay.json()["error"] == "challenge_unknown"


@pytest.mark.asyncio
async def test_an_unreachable_chain_refuses_the_job_with_503_not_a_crash(
    jobs: tuple[JobService, Any, fakeredis.FakeRedis],
) -> None:
    async def unreachable(_task_id: str) -> str | None:
        raise ConnectionError("rpc down")

    payload = await signed_body(tensor_bytes(seed=63))
    app.state.challenges.lookup = unreachable
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/jobs", json=payload)

    assert response.status_code == 503
    assert response.json()["error"] == "auth_unavailable"
