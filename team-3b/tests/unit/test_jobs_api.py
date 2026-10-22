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
from typing import Any

import fakeredis.aioredis as fakeredis
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from common.model_registry import ModelRegistry
from common.models import ProofArtifact
from inference.app import app
from inference.engine import InferenceEngine
from inference.jobs import INDEX_KEY, INPUT_KEY, JobService
from inference.tee_runtime import TEERuntime
from proof_generator.verifier import ProofVerifier
from proof_generator.zkml_prover import ZkMLProver
from tests.onnx_fixtures import linear_model_bytes, tensor_bytes
from tests.property.test_model_registry_prop import MockStorageBackend

TASK_ID = "0x" + "ab" * 32
MODEL_ID = "job_model"


def body(input_data: bytes, model_id: str = MODEL_ID, task_id: str = TASK_ID) -> dict[str, Any]:
    return {
        "task_id": task_id,
        "model_id": model_id,
        "input_hash": hashlib.sha256(input_data).hexdigest(),
        "input_data": base64.b64encode(input_data).decode(),
    }


async def wait_for_status(client: AsyncClient, job_id: str, statuses: set[str], timeout: float = 20.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while True:
        response = await client.get(f"/jobs/{job_id}")
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

    service = JobService(redis_client, engine, tee, prover)
    state = app.state
    state.redis_client = redis_client
    state.storage_backend = storage
    state.model_registry = registry
    state.tee_runtime = tee
    state.inference_engine = engine
    state.zkml_prover = prover
    state.job_service = service

    yield service, engine, redis_client
    await service.shutdown()


@pytest.mark.asyncio
async def test_post_returns_202_and_the_completed_receipt_carries_attestation_and_proof(
    jobs: tuple[JobService, Any, fakeredis.FakeRedis],
) -> None:
    input_data = tensor_bytes(seed=3)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/jobs", json=body(input_data))
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
        accepted = (await client.post("/jobs", json=body(input_data))).json()
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
        first = (await client.post("/jobs", json=body(input_data))).json()
        second_response = await client.post("/jobs", json=body(input_data))
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
        first = (await client.post("/jobs", json=body(input_data))).json()
        conflict = await client.post("/jobs", json=body(input_data, model_id="another_model"))

    assert conflict.status_code == 409
    assert conflict.json()["error"] == "JobConflict"
    assert first["model_id"] == MODEL_ID


@pytest.mark.asyncio
async def test_an_input_that_does_not_match_its_hash_is_rejected(jobs: tuple[JobService, Any, fakeredis.FakeRedis]) -> None:
    input_data = tensor_bytes(seed=11)
    payload = body(input_data)
    payload["input_hash"] = hashlib.sha256(b"something else").hexdigest()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/jobs", json=payload)

    assert response.status_code == 422
    assert response.json()["error"] == "InvalidJob"


@pytest.mark.asyncio
async def test_an_input_that_is_not_a_tensor_is_rejected(jobs: tuple[JobService, Any, fakeredis.FakeRedis]) -> None:
    not_a_tensor = b"not an npy tensor"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/jobs", json=body(not_a_tensor))

    assert response.status_code == 422
    assert "not an .npy tensor" in response.json()["detail"]


@pytest.mark.asyncio
async def test_a_malformed_task_id_or_hash_is_rejected_by_the_schema(jobs: tuple[JobService, Any, fakeredis.FakeRedis]) -> None:
    input_data = tensor_bytes(seed=13)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        bad_task = await client.post("/jobs", json=body(input_data, task_id="0x1234"))
        bad_base64 = await client.post("/jobs", json={**body(input_data), "input_data": "!!not base64!!"})

    assert bad_task.status_code == 422
    assert bad_base64.status_code == 422


@pytest.mark.asyncio
async def test_an_unknown_model_fails_the_job_with_an_error_code(jobs: tuple[JobService, Any, fakeredis.FakeRedis]) -> None:
    input_data = tensor_bytes(seed=15)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        accepted = (await client.post("/jobs", json=body(input_data, model_id="not_registered"))).json()
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
        accepted = (await client.post("/jobs", json=body(input_data))).json()
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
        accepted = (await client.post("/jobs", json=body(input_data))).json()
        running = await client.get(f"/jobs/{accepted['job_id']}")
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
        accepted = (await client.post("/jobs", json=body(input_data))).json()
        job = await wait_for_status(client, accepted["job_id"], {"completed", "failed"})
        finished = await client.get(f"/jobs/{accepted['job_id']}")

    assert accepted["deadline"] >= before + 150
    assert job["deadline"] == accepted["deadline"]
    assert "retry-after" not in finished.headers
