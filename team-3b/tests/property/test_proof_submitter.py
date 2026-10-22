import asyncio
import hashlib
import json
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import fakeredis.aioredis as fakeredis
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from common.constants import DEVELOPMENT_PROOF_BACKEND
from common.models import AttestationReport, ProofArtifact
from proof_submitter import ProofSubmitter
from proof_submitter.submitter import PendingProof


def payload(task_id: str, output_hash: str = "output") -> tuple[str, AttestationReport, ProofArtifact, str]:
    attestation = AttestationReport(
        enclave_measurement="measurement",
        signer_identity="signer",
        input_hash="input",
        output_hash=output_hash,
        hardware_signature=b"signature",
        timestamp=1.0,
    )
    # Batching and dead-letter behaviour is under test here, not the backend, so the proof
    # is a development-backend stand-in and the submitter opts in to it explicitly.
    proof = ProofArtifact(
        proof_bytes=b"proof-" + task_id.encode(),
        public_inputs={"output_hash": output_hash},
        verification_key_ref="vk",
        backend=DEVELOPMENT_PROOF_BACKEND,
    )
    return task_id, attestation, proof, "QmModel"


def submitter(sleep: Callable[[float], Any] | None = None) -> ProofSubmitter:
    return ProofSubmitter(
        web3=SimpleNamespace(),
        contract=SimpleNamespace(),
        redis_client=fakeredis.FakeRedis(decode_responses=False),
        sleep=sleep or (lambda delay: asyncio.sleep(0)),
        batch_window=10.0,
        allow_development_backend=True,
    )


@settings(max_examples=20, deadline=None)
@given(task_id=st.text(min_size=1, max_size=20))
@pytest.mark.asyncio
async def test_property_16_proof_hash_contains_proof_and_signature(task_id: str) -> None:
    instance = submitter()
    args = payload(task_id)

    pending = await instance.submit_proof(*args)

    assert pending.proof_hash == hashlib.sha256(args[2].proof_bytes + args[1].hardware_signature).hexdigest()
    instance._batch_timer.cancel()  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_property_16_transaction_payload_contains_all_batch_arrays() -> None:
    captured: dict[str, Any] = {}

    class Functions:
        def submitProofs(
            self, task_ids: list[bytes], proof_hashes: list[bytes], model_cids: list[str], result_hashes: list[bytes]
        ) -> "Functions":
            captured.update({"task_ids": task_ids, "proof_hashes": proof_hashes, "model_cids": model_cids, "result_hashes": result_hashes})
            return self

        def build_transaction(self, options: dict[str, Any]) -> dict[str, Any]:
            return options

        def transact(self, options: dict[str, Any]) -> str:
            return "0xtx"

    class Web3:
        class Eth:
            accounts = ["0x0000000000000000000000000000000000000001"]

            @staticmethod
            def get_transaction_count(_account: str) -> int:
                return 0

            @staticmethod
            def wait_for_transaction_receipt(_tx: str) -> dict[str, int]:
                return {"status": 1}

        eth = Eth()

    instance = ProofSubmitter(
        Web3(),
        SimpleNamespace(functions=Functions()),
        fakeredis.FakeRedis(decode_responses=False),
        allow_development_backend=True,
    )
    proofs = [await instance.submit_proof(*payload("payload-one")), await instance.submit_proof(*payload("payload-two"))]
    instance._batch_timer.cancel()  # type: ignore[union-attr]
    await instance._send_transaction(proofs)

    assert len(captured["task_ids"]) == len(captured["proof_hashes"]) == len(captured["model_cids"]) == len(captured["result_hashes"]) == 2


@pytest.mark.asyncio
async def test_property_17_proofs_in_window_share_one_batch() -> None:
    instance = submitter()
    captured = []
    instance._submit_batch = lambda proofs: captured.append(proofs) or asyncio.sleep(0)  # type: ignore[method-assign, func-returns-value]

    await instance.submit_proof(*payload("one"))
    await instance.submit_proof(*payload("two"))
    await instance._flush_after_window()

    assert len(captured) == 1
    assert [proof.task_id for proof in captured[0]] == ["one", "two"]


@pytest.mark.asyncio
async def test_property_18_three_failures_persist_dlq_and_alert(caplog: pytest.LogCaptureFixture) -> None:
    instance = submitter()
    attempts = []

    async def fail(_proofs: list[PendingProof]) -> str:
        attempts.append(True)
        raise RuntimeError("rpc unavailable")

    instance._send_transaction = fail  # type: ignore[method-assign, assignment]
    instance.sleep = lambda delay: asyncio.sleep(0)
    proof = await instance.submit_proof(*payload("failed"))
    instance._batch_timer.cancel()  # type: ignore[union-attr]
    with caplog.at_level("ERROR"):
        await instance._submit_batch([proof])

    entries = await instance.redis.lrange(instance.DLQ_KEY, 0, -1)
    assert len(attempts) == 3
    assert len(entries) == 1
    assert "PROOF_SUBMISSION_FAILED_PERMANENTLY" in caplog.text
    assert json.loads(entries[0])["proofs"][0]["task_id"] == "failed"


@pytest.mark.asyncio
async def test_batch_timer_resets_and_future_proof_starts_new_window() -> None:
    instance = submitter()
    batches = []
    instance._submit_batch = lambda proofs: batches.append(proofs) or asyncio.sleep(0)  # type: ignore[method-assign, func-returns-value]

    await instance.submit_proof(*payload("first"))
    first_timer = instance._batch_timer
    await instance._flush_after_window()
    assert instance._batch_timer is None
    await instance.submit_proof(*payload("second"))
    assert instance._batch_timer is not first_timer
    await instance._flush_after_window()
    assert [proof.task_id for proof in batches[1]] == ["second"]


@pytest.mark.asyncio
async def test_retry_succeeds_without_dlq() -> None:
    instance = submitter()
    calls = 0

    async def send(_proofs: list[PendingProof]) -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("temporary")
        return "0xtx"

    instance._send_transaction = send  # type: ignore[method-assign, assignment]
    pending = await instance.submit_proof(*payload("retry"))
    instance._batch_timer.cancel()  # type: ignore[union-attr]
    await instance._submit_batch([pending])

    assert calls == 2
    assert await instance.redis.llen(instance.DLQ_KEY) == 0


@pytest.mark.asyncio
async def test_retrying_batch_does_not_capture_new_batch() -> None:
    instance = submitter()
    attempts = 0
    retry_started = asyncio.Event()
    release_retry = asyncio.Event()
    submitted = []

    async def send(proofs: list[PendingProof]) -> str:
        nonlocal attempts
        attempts += 1
        submitted.append([proof.task_id for proof in proofs])
        if attempts == 1:
            retry_started.set()
            raise RuntimeError("temporary")
        return "0xtx"

    instance._send_transaction = send  # type: ignore[method-assign]
    instance.sleep = lambda delay: release_retry.wait()
    old_proof = await instance.submit_proof(*payload("old"))
    instance._batch_timer.cancel()  # type: ignore[union-attr]
    instance._pending_proofs.clear()
    retry_task = asyncio.create_task(instance._submit_batch([old_proof]))
    await retry_started.wait()
    await instance.submit_proof(*payload("new"))
    instance._batch_timer.cancel()  # type: ignore[union-attr]
    release_retry.set()
    await retry_task

    assert submitted == [["old"], ["old"]]
    assert [proof.task_id for proof in instance._pending_proofs] == ["new"]


@pytest.mark.asyncio
async def test_business_revert_uses_same_retry_and_dlq_path() -> None:
    instance = submitter()
    proof = await instance.submit_proof(*payload("business-revert"))
    instance._batch_timer.cancel()  # type: ignore[union-attr]

    async def revert(_proofs: list[PendingProof]) -> str:
        raise RuntimeError("contract reverted: invalid task state")

    instance._send_transaction = revert  # type: ignore[method-assign, assignment]
    await instance._submit_batch([proof])

    entry = json.loads((await instance.redis.lindex(instance.DLQ_KEY, 0)).decode())
    assert entry["last_error"] == "contract reverted: invalid task state"
    assert instance.metrics["retries"] == 2


@pytest.mark.asyncio
async def test_concurrent_failed_batches_append_distinct_dlq_entries() -> None:
    instance = submitter()
    instance.sleep = lambda delay: asyncio.sleep(0)

    async def fail(_proofs: list[PendingProof]) -> str:
        raise RuntimeError("concurrent failure")

    instance._send_transaction = fail  # type: ignore[method-assign, assignment]
    first = await instance.submit_proof(*payload("concurrent-one"))
    second = await instance.submit_proof(*payload("concurrent-two"))
    instance._batch_timer.cancel()  # type: ignore[union-attr]
    await asyncio.gather(instance._submit_batch([first]), instance._submit_batch([second]))

    entries = [json.loads(raw.decode()) for raw in await instance.redis.lrange(instance.DLQ_KEY, 0, -1)]
    assert {entry["proofs"][0]["task_id"] for entry in entries} == {"concurrent-one", "concurrent-two"}


@pytest.mark.asyncio
async def test_retrying_a_failed_chunk_never_resends_an_accepted_chunk() -> None:
    instance = submitter()
    instance._max_batch_size = lambda: 2  # type: ignore[method-assign]
    sent: list[list[str]] = []
    failures_left = [1]

    def send_chunk(proofs: list[PendingProof]) -> tuple[str, list[PendingProof]]:
        task_ids = [proof.task_id for proof in proofs]
        sent.append(task_ids)
        if task_ids == ["third", "fourth"] and failures_left[0]:
            failures_left[0] -= 1
            raise RuntimeError("transient RPC failure")
        return "0xtx", []

    instance._send_chunk = send_chunk  # type: ignore[method-assign]
    proofs = []
    for task_id in ("first", "second", "third", "fourth"):
        _, attestation, zkml_proof, model_cid = payload(task_id)
        proofs.append(PendingProof(task_id, attestation, zkml_proof, model_cid, f"hash-{task_id}", "result"))

    assert await instance._submit_batch(proofs) == "0xtx"
    assert sent == [["first", "second"], ["third", "fourth"], ["third", "fourth"]]
