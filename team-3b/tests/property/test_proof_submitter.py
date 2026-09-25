import asyncio
import hashlib
import json
from types import SimpleNamespace

import fakeredis.aioredis as fakeredis
import pytest
from hypothesis import given, settings, strategies as st

from common.models import AttestationReport, ProofArtifact
from proof_submitter import ProofSubmitter


def payload(task_id: str, output_hash: str = "output"):
    attestation = AttestationReport(
        enclave_measurement="measurement",
        signer_identity="signer",
        input_hash="input",
        output_hash=output_hash,
        hardware_signature=b"signature",
        timestamp=1.0,
    )
    proof = ProofArtifact(proof_bytes=b"proof-" + task_id.encode(), public_inputs={"output_hash": output_hash}, verification_key_ref="vk")
    return task_id, attestation, proof, "QmModel"


def submitter(sleep=None):
    return ProofSubmitter(
        web3=SimpleNamespace(),
        contract=SimpleNamespace(),
        redis_client=fakeredis.FakeRedis(decode_responses=False),
        sleep=sleep or (lambda delay: asyncio.sleep(0)),
        batch_window=10.0,
    )


@settings(max_examples=20, deadline=None)
@given(task_id=st.text(min_size=1, max_size=20))
@pytest.mark.asyncio
async def test_property_16_proof_hash_contains_proof_and_signature(task_id):
    instance = submitter()
    args = payload(task_id)

    pending = await instance.submit_proof(*args)

    assert pending.proof_hash == hashlib.sha256(args[2].proof_bytes + args[1].hardware_signature).hexdigest()
    instance._batch_timer.cancel()


@pytest.mark.asyncio
async def test_property_17_proofs_in_window_share_one_batch():
    instance = submitter()
    captured = []
    instance._submit_batch = lambda proofs: captured.append(proofs) or asyncio.sleep(0)

    await instance.submit_proof(*payload("one"))
    await instance.submit_proof(*payload("two"))
    await instance._flush_after_window()

    assert len(captured) == 1
    assert [proof.task_id for proof in captured[0]] == ["one", "two"]


@pytest.mark.asyncio
async def test_property_18_three_failures_persist_dlq_and_alert(caplog):
    instance = submitter()
    attempts = []

    async def fail(_proofs):
        attempts.append(True)
        raise RuntimeError("rpc unavailable")

    instance._send_transaction = fail
    instance.sleep = lambda delay: asyncio.sleep(0)
    proof = await instance.submit_proof(*payload("failed"))
    instance._batch_timer.cancel()
    with caplog.at_level("ERROR"):
        await instance._submit_batch([proof])

    entries = await instance.redis.lrange(instance.DLQ_KEY, 0, -1)
    assert len(attempts) == 3
    assert len(entries) == 1
    assert "PROOF_SUBMISSION_FAILED_PERMANENTLY" in caplog.text
    assert json.loads(entries[0])["proofs"][0]["task_id"] == "failed"


@pytest.mark.asyncio
async def test_batch_timer_resets_and_future_proof_starts_new_window():
    instance = submitter()
    batches = []
    instance._submit_batch = lambda proofs: batches.append(proofs) or asyncio.sleep(0)

    await instance.submit_proof(*payload("first"))
    first_timer = instance._batch_timer
    await instance._flush_after_window()
    assert instance._batch_timer is None
    await instance.submit_proof(*payload("second"))
    assert instance._batch_timer is not first_timer
    await instance._flush_after_window()
    assert [proof.task_id for proof in batches[1]] == ["second"]


@pytest.mark.asyncio
async def test_retry_succeeds_without_dlq():
    instance = submitter()
    calls = 0

    async def send(_proofs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("temporary")
        return "0xtx"

    instance._send_transaction = send
    await instance._submit_batch([await instance.submit_proof(*payload("retry"))])

    assert calls == 2
    assert await instance.redis.llen(instance.DLQ_KEY) == 0


@pytest.mark.asyncio
async def test_retrying_batch_does_not_capture_new_batch():
    instance = submitter()
    attempts = 0
    retry_started = asyncio.Event()
    release_retry = asyncio.Event()
    submitted = []

    async def send(proofs):
        nonlocal attempts
        attempts += 1
        submitted.append([proof.task_id for proof in proofs])
        if attempts == 1:
            retry_started.set()
            raise RuntimeError("temporary")
        return "0xtx"

    instance._send_transaction = send
    instance.sleep = lambda delay: release_retry.wait()
    old_proof = await instance.submit_proof(*payload("old"))
    instance._batch_timer.cancel()
    instance._pending_proofs.clear()
    retry_task = asyncio.create_task(instance._submit_batch([old_proof]))
    await retry_started.wait()
    await instance.submit_proof(*payload("new"))
    instance._batch_timer.cancel()
    release_retry.set()
    await retry_task

    assert submitted == [["old"], ["old"]]
    assert [proof.task_id for proof in instance._pending_proofs] == ["new"]