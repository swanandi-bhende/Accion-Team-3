"""Step 16.6 backpressure/concurrency and Step 16.8 cross-stage failure injection.

The point of these tests is the paths between stages: a stage that fails must
leave the pipeline able to process the next task, and a burst of tasks must not
run more concurrent TEE inferences than the configured cap.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
from collections.abc import Iterable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import fakeredis.aioredis as fakeredis
import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from common.interfaces import StorageBackend
from common.model_registry import ModelRegistry
from common.models import (
    AttestationReport,
    ExecutionReceipt,
    InferenceRequest,
    ProofArtifact,
)
from inference.engine import InferenceEngine
from inference.pipeline import (
    OUTCOME_DROPPED_INTEGRITY_FAILURE,
    OUTCOME_DROPPED_PROOF_TIMEOUT,
    OUTCOME_DROPPED_SUBMISSION_FAILED,
    OUTCOME_SUBMITTED,
    Pipeline,
)
from inference.tee_runtime import TEERuntime
from proof_generator.zkml_prover import ZkMLProver
from proof_submitter.submitter import PendingProof, ProofSubmitter
from tests import chain_helper

SEALED_KEY = b"mock_sealed_key_0123456789abcdef"
MODEL_BINARY = b"MOCK_MLP_WEIGHTS_67890"
SLOW_MODEL = "dd" * 32
FAST_MODEL = "ee" * 32


def _task(task_id: str, model_id: str) -> dict[str, Any]:
    """Exactly the payload shape EventListener dispatches (see listener._try_dispatch)."""
    return {
        "created": {"task_id": task_id, "description_hash": bytes.fromhex(model_id)},
        "funded": {"task_id": task_id, "funded_amount": 1},
        "dispatched": True,
    }


class DelayedEngine:
    """InferenceEngine double that records its own peak concurrency."""

    def __init__(self, delay: float = 0.02) -> None:
        self.delay = delay
        self.active = 0
        self.peak = 0
        self.calls = 0

    async def execute(self, request: InferenceRequest) -> ExecutionReceipt:
        self.calls += 1
        self.active += 1
        self.peak = max(self.peak, self.active)
        try:
            await asyncio.sleep(self.delay)
            output = b"fake-output:" + request.input_data
            return ExecutionReceipt(
                execution_id=f"exec-{self.calls}",
                model_id=request.model_id,
                model_cid="QmFakeModel",
                input_hash=hashlib.sha256(request.input_data).hexdigest(),
                output_hash=hashlib.sha256(output).hexdigest(),
                output=output,
                timestamp=time.time(),
            )
        finally:
            self.active -= 1


class FakeTee:
    def generate_attestation(self, input_data: bytes, output: bytes) -> AttestationReport:
        return AttestationReport(
            enclave_measurement="fake-measurement",
            signer_identity="fake-signer",
            input_hash=hashlib.sha256(input_data).hexdigest(),
            output_hash=hashlib.sha256(output).hexdigest(),
            hardware_signature=b"fake-signature",
            timestamp=time.time(),
        )


class FakeProver:
    """ZkMLProver double that times out for the models named in ``timeout_for``."""

    def __init__(self, timeout_for: Iterable[str] = ()) -> None:
        self.timeout_for = set(timeout_for)

    async def generate_proof(self, receipt: ExecutionReceipt) -> ProofArtifact:
        if receipt.model_id in self.timeout_for:
            raise TimeoutError("proof generation exceeded its timeout")
        return ProofArtifact(
            proof_bytes=b"FAKEPROOF",
            public_inputs={
                "input_hash": receipt.input_hash,
                "output_hash": receipt.output_hash,
                "model_hash": "fake-model-hash",
                "architecture": "mlp",
            },
            verification_key_ref="data/proving_keys/mlp.vk",
        )


class RecordingSubmitter:
    """ProofSubmitter double that settles immediately, or always dead-letters."""

    def __init__(self, settle: bool = True) -> None:
        self.settle = settle
        self.submissions: list[tuple[str, AttestationReport, ProofArtifact, str]] = []
        self.metrics = {
            "batches_submitted": 0,
            "proofs_submitted": 0,
            "retries": 0,
            "dlq_batches": 0,
            "last_batch_size": 0,
        }
        self._buffered: list[str] = []

    async def submit_proof(
        self,
        task_id: str,
        attestation: AttestationReport,
        zkml_proof: ProofArtifact,
        model_cid: str,
    ) -> SimpleNamespace:
        self.submissions.append((task_id, attestation, zkml_proof, model_cid))
        self._buffered.append(task_id)
        return SimpleNamespace(task_id=task_id, proof_hash="a" * 64)

    def buffered_task_ids(self) -> list[str]:
        return list(self._buffered)

    async def flush(self) -> str | None:
        if not self._buffered:
            return None
        self._buffered.clear()
        if not self.settle:
            self.metrics["dlq_batches"] += 1
            return None
        self.metrics["batches_submitted"] += 1
        self.metrics["proofs_submitted"] += 1
        return "0x" + "de" * 32


def _fake_pipeline(
    engine: DelayedEngine | InferenceEngine,
    prover: FakeProver | ZkMLProver | None = None,
    submitter: RecordingSubmitter | None = None,
    *,
    max_in_flight: int = 4,
    queue_maxsize: int | None = None,
) -> Pipeline:
    return Pipeline(
        SimpleNamespace(),  # the listener is only the dispatch sink here
        engine,
        FakeTee(),
        prover or FakeProver(),
        submitter or RecordingSubmitter(),
        max_in_flight=max_in_flight,
        queue_maxsize=queue_maxsize,
    )


# --------------------------------------------------------------------------- #
# Step 16.6 - backpressure and concurrency limits
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_inference_concurrency_is_capped_and_reported() -> None:
    """Nine tasks through three workers: the TEE never sees more than three at once.

    The worker pool runs here rather than ``drain()``, so the admission path is
    exercised too: the queue (6 slots) fills, later enqueues wait for a worker to
    free a slot, and the inference semaphore is what bounds concurrency.
    """
    engine = DelayedEngine(delay=0.02)
    pipeline = _fake_pipeline(engine, max_in_flight=3)
    pipeline._spawn_workers()

    for index in range(9):
        await pipeline.enqueue(_task(f"t{index}", FAST_MODEL))
    await pipeline._queue.join()
    await pipeline.stop()

    records = pipeline.records
    assert len(records) == 9
    assert pipeline.stats()["queued"] == 0
    assert {record.outcome for record in records} == {OUTCOME_SUBMITTED}
    assert engine.calls == 9
    assert engine.peak == 3
    assert pipeline.stats()["peak_inference_in_flight"] == 3
    assert pipeline.stats()["max_in_flight"] == 3


@pytest.mark.asyncio
async def test_full_queue_applies_backpressure_and_logs_being_throttled() -> None:
    pipeline = _fake_pipeline(DelayedEngine(delay=0.01), max_in_flight=2, queue_maxsize=2)
    pipeline._queue.put_nowait(_task("t0", FAST_MODEL))
    pipeline._queue.put_nowait(_task("t1", FAST_MODEL))

    blocked = asyncio.create_task(pipeline.enqueue(_task("t2", FAST_MODEL)))
    await asyncio.sleep(0.01)

    # the admission path reported throttling and actually waited for a slot
    assert pipeline.stats()["throttled_events"] == 1
    assert not blocked.done()
    assert pipeline.stats()["queued"] == 2

    assert len(await pipeline.drain()) == 2
    await blocked
    assert pipeline.stats()["queued"] == 1

    records = await pipeline.drain()
    assert [record.outcome for record in records] == [OUTCOME_SUBMITTED]
    assert pipeline.stats()["outcome_counts"] == {OUTCOME_SUBMITTED: 3}


# --------------------------------------------------------------------------- #
# Step 16.8 - failure injection across stage boundaries
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_tee_integrity_failure_drops_that_task_only(memory_storage: StorageBackend) -> None:
    """A model whose plaintext cannot match its committed hash is dropped, not retried.

    ``InferenceEngine`` reaches the real ``TEERuntime`` here: the blob is a
    correctly sealed artifact, but the registry committed to a different hash, so
    the enclave's integrity check is the thing that fails. The next task still
    completes.
    """
    redis_client: Any = fakeredis.FakeRedis(decode_responses=False)
    registry = ModelRegistry(redis_client, memory_storage)

    nonce = os.urandom(12)
    sealed_blob = nonce + AESGCM(SEALED_KEY).encrypt(nonce, MODEL_BINARY, None)
    broken_model_id = "bb" * 32
    cid = await memory_storage.upload(sealed_blob)
    key = f"model:{broken_model_id}:v1"
    await redis_client.hset(
        key,
        mapping={
            "version": "1",
            "cid": cid,
            "hash": "00" * 32,
            "architecture": "mlp",
        },
    )
    await redis_client.set(f"model:{broken_model_id}:latest", key)
    await registry.upload_model(FAST_MODEL, MODEL_BINARY, architecture="mlp")

    tee = TEERuntime("sgx")
    tee.initialize()
    engine = InferenceEngine(registry, tee, redis_client, timeout_s=30.0, sealed_key=SEALED_KEY)
    pipeline = Pipeline(
        SimpleNamespace(), engine, tee, FakeProver(), RecordingSubmitter(), max_in_flight=2
    )

    await pipeline.enqueue(_task("t-broken", broken_model_id))
    await pipeline.enqueue(_task("t-good", FAST_MODEL))
    records = await pipeline.drain()

    assert [record.outcome for record in records] == [
        OUTCOME_DROPPED_INTEGRITY_FAILURE,
        OUTCOME_SUBMITTED,
    ]
    assert "Integrity check failed" in records[0].detail
    assert records[0].execution_id is None  # dropped before a receipt ever existed
    assert records[0].proof_s is None
    assert records[1].execution_id is not None
    assert pipeline.stats()["outcome_counts"] == {
        OUTCOME_DROPPED_INTEGRITY_FAILURE: 1,
        OUTCOME_SUBMITTED: 1,
    }


@pytest.mark.asyncio
async def test_proof_timeout_drops_one_task_without_blocking_the_next() -> None:
    submitter = RecordingSubmitter()
    pipeline = _fake_pipeline(
        DelayedEngine(delay=0.0),
        prover=FakeProver(timeout_for=[SLOW_MODEL]),
        submitter=submitter,
        max_in_flight=2,
    )

    await pipeline.enqueue(_task("t-slow", SLOW_MODEL))
    await pipeline.enqueue(_task("t-fast", FAST_MODEL))
    records = await pipeline.drain()

    assert [record.outcome for record in records] == [
        OUTCOME_DROPPED_PROOF_TIMEOUT,
        OUTCOME_SUBMITTED,
    ]
    # the timed-out task got past inference, so the failure is provably at stage 3
    assert records[0].inference_s is not None
    assert records[0].execution_id is not None
    assert records[0].proof_s is None
    assert records[0].tx_hash is None
    # only the healthy task ever reached the submitter
    assert records[1].submit_s is not None
    assert [task_id for task_id, *_ in submitter.submissions] == ["t-fast"]


@pytest.mark.asyncio
async def test_unauthenticated_model_blob_is_dropped_as_an_integrity_failure(
    memory_storage: StorageBackend,
) -> None:
    """Storage handing back bytes that are not the committed sealed artifact.

    The blob decrypts to nothing meaningful, so this must land in the integrity
    bucket instead of the catch-all "unexpected error" bucket.
    """
    redis_client: Any = fakeredis.FakeRedis(decode_responses=False)
    registry = ModelRegistry(redis_client, memory_storage)
    tampered_model_id = "cc" * 32
    cid = await memory_storage.upload(b"not-a-sealed-model-blob")
    key = f"model:{tampered_model_id}:v1"
    await redis_client.hset(
        key,
        mapping={
            "version": "1",
            "cid": cid,
            "hash": "11" * 32,
            "architecture": "mlp",
        },
    )
    await redis_client.set(f"model:{tampered_model_id}:latest", key)

    tee = TEERuntime("sgx")
    tee.initialize()
    engine = InferenceEngine(registry, tee, redis_client, timeout_s=30.0, sealed_key=SEALED_KEY)
    pipeline = _fake_pipeline(engine, max_in_flight=1)

    await pipeline.enqueue(_task("t-tampered", tampered_model_id))
    records = await pipeline.drain()

    assert [record.outcome for record in records] == [OUTCOME_DROPPED_INTEGRITY_FAILURE]
    assert records[0].execution_id is None
    assert records[0].detail.startswith("Integrity check failed")


@pytest.mark.asyncio
async def test_real_prover_timeout_maps_to_a_dropped_task(
    memory_storage: StorageBackend, tmp_path: Path
) -> None:
    """The real ZkMLProver's own timeout, not a fake raising TimeoutError."""
    redis_client = fakeredis.FakeRedis(decode_responses=False)
    registry = ModelRegistry(redis_client, memory_storage)
    await registry.upload_model(FAST_MODEL, MODEL_BINARY, architecture="mlp")
    prover = ZkMLProver(registry, proving_key_store=str(tmp_path / "keys"), timeout_s=0.0)
    pipeline = _fake_pipeline(DelayedEngine(delay=0.0), prover=prover, max_in_flight=1)

    await pipeline.enqueue(_task("t-timeout", FAST_MODEL))
    records = await pipeline.drain()

    assert [record.outcome for record in records] == [OUTCOME_DROPPED_PROOF_TIMEOUT]
    assert "exceeded its timeout" in records[0].detail


@pytest.mark.asyncio
async def test_exhausted_submission_retries_land_in_dlq_and_the_pipeline_continues(
    team_a_chain: tuple[Any, Any, Any],
) -> None:
    """The first batch reverts until it is dead-lettered; the next task still settles."""
    web3, task_manager, escrow = team_a_chain
    redis_client: Any = fakeredis.FakeRedis(decode_responses=False)
    submitter = ProofSubmitter(web3, task_manager, redis_client, batch_window=10.0)
    # Retries must be instant here; the batch window stays realistic so the
    # background flush timer cannot race the explicit flush.
    submitter.RETRY_INTERVAL = 0.0

    real_send_transaction = submitter._send_transaction
    attempts = {"count": 0}

    async def fail_every_attempt_of_the_first_batch(proofs: list[PendingProof]) -> str:
        attempts["count"] += 1
        if attempts["count"] <= submitter.MAX_RETRIES:
            raise RuntimeError("execution reverted")
        return await real_send_transaction(proofs)

    submitter._send_transaction = fail_every_attempt_of_the_first_batch  # type: ignore[method-assign]
    pipeline = Pipeline(
        SimpleNamespace(),
        DelayedEngine(delay=0.0),
        FakeTee(),
        FakeProver(),
        submitter,
        max_in_flight=1,
        flush_batches=True,
    )

    await pipeline.enqueue(_task("t-a", FAST_MODEL))
    failed = await pipeline.drain()

    assert [record.outcome for record in failed] == [OUTCOME_DROPPED_SUBMISSION_FAILED]
    assert "dead-lettered" in failed[0].detail
    assert submitter.metrics["dlq_batches"] == 1
    assert attempts["count"] == submitter.MAX_RETRIES

    dlq = await redis_client.lrange(ProofSubmitter.DLQ_KEY, 0, -1)
    assert len(dlq) == 1
    payload = json.loads(dlq[0])
    assert payload["proofs"][0]["task_id"] == "t-a"
    assert "execution reverted" in payload["last_error"]

    # the pipeline is not stuck: the very next task settles on chain. Team A's
    # TaskManager only accepts proofs for tasks that actually exist, so this one
    # is created and funded on chain first.
    real_task_id, _created, _funded = chain_helper.create_and_fund(
        web3, task_manager, escrow, chain_helper.token_contract(web3)
    )
    await pipeline.enqueue(_task(real_task_id.hex(), FAST_MODEL))
    settled = await pipeline.drain()

    assert [record.outcome for record in settled] == [OUTCOME_SUBMITTED]
    assert settled[0].tx_hash is not None
    assert web3.eth.get_transaction_receipt(settled[0].tx_hash).status == 1
    assert submitter.metrics["proofs_submitted"] == 1
    assert attempts["count"] == submitter.MAX_RETRIES + 1  # the dead-lettered batch, then t-b
    assert pipeline.stats()["outcome_counts"] == {
        OUTCOME_DROPPED_SUBMISSION_FAILED: 1,
        OUTCOME_SUBMITTED: 1,
    }
