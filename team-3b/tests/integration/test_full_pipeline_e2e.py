"""Step 16.7: the whole internal pipeline, end to end, against the mock chain.

One task walks task event -> inference -> TEE attestation + zkML proof ->
on-chain submission -> independent verification. Every hop is asserted against a
real component; only storage is a double, because no IPFS node runs here.

The second test repeats that same real journey a few times to give Step 16.9 a
measured dispatch-to-confirmation distribution instead of a single sample.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import fakeredis.aioredis as fakeredis
import pytest

from common.interfaces import StorageBackend
from common.model_registry import ModelRegistry
from common.models import AttestationReport, ProofArtifact, VerificationRequest
from event_listener.listener import EventListener
from inference.enclave_sim import EnclaveSim
from inference.engine import InferenceEngine
from inference.pipeline import OUTCOME_SUBMITTED, Pipeline
from inference.tee_runtime import TEERuntime
from proof_generator.zkml_prover import ZkMLProver
from proof_submitter.submitter import PendingProof, ProofSubmitter
from verification.interface import VerificationInterface
from verification.resolver import StorageProofResolver
from verification.verifiers import (
    ConcreteTEEVerifier,
    ConcreteZkMLVerifier,
    serialize_attestation,
    serialize_proof_artifact,
)

MODEL_ARCHITECTURE = "mlp"
MODEL_BINARY = b"MOCK_MLP_WEIGHTS_67890"
BATCH_SIZE = 6

FundedTaskFactory = Callable[[bytes], tuple[bytes, Any, Any]]


class CapturingSubmitter:
    """Forward every call to the real submitter, recording what it was handed."""

    def __init__(self, inner: ProofSubmitter) -> None:
        self.inner = inner
        self.submissions: list[tuple[str, AttestationReport, ProofArtifact, str]] = []

    async def submit_proof(
        self,
        task_id: str,
        attestation: AttestationReport,
        zkml_proof: ProofArtifact,
        model_cid: str,
    ) -> PendingProof:
        self.submissions.append((task_id, attestation, zkml_proof, model_cid))
        return await self.inner.submit_proof(task_id, attestation, zkml_proof, model_cid)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)


@pytest.fixture
def wired(
    mock_chain: tuple[Any, Any, Any], memory_storage: StorageBackend, tmp_path: Path
) -> dict[str, Any]:
    """Real components end to end; only storage/IPFS is a double."""
    web3, task_manager, escrow = mock_chain
    redis_client = fakeredis.FakeRedis(decode_responses=False)
    registry = ModelRegistry(redis_client, memory_storage)

    tee = TEERuntime("sgx")
    tee.initialize()
    assert isinstance(tee.enclave, EnclaveSim)
    engine = InferenceEngine(registry, tee, redis_client, timeout_s=30.0)
    prover = ZkMLProver(registry, proving_key_store=str(tmp_path / "proving_keys"))
    submitter = CapturingSubmitter(
        ProofSubmitter(web3, task_manager, redis_client, batch_window=10.0)
    )
    listener = EventListener(web3, task_manager, escrow, redis_client)
    interface = VerificationInterface(
        ConcreteTEEVerifier(
            trusted_public_keys={tee.enclave.signer_id: tee.enclave.public_key_bytes}
        ),
        ConcreteZkMLVerifier(),
        StorageProofResolver(storage=memory_storage),
    )
    pipeline = Pipeline(
        listener,
        engine,
        tee,
        prover,
        submitter,
        verification=interface,
        max_in_flight=4,
        flush_batches=True,
    )
    return {
        "pipeline": pipeline,
        "registry": registry,
        "web3": web3,
        "redis": redis_client,
        "tee": tee,
        "interface": interface,
    }


@pytest.mark.asyncio
async def test_full_pipeline_settles_and_verifies_one_task(
    wired: dict[str, Any], create_funded_task: FundedTaskFactory
) -> None:
    pipeline, registry = wired["pipeline"], wired["registry"]
    web3, redis_client = wired["web3"], wired["redis"]
    tee, interface = wired["tee"], wired["interface"]

    description_hash = web3.keccak(text="pipeline-e2e-model")
    model_id = description_hash.hex()
    await registry.upload_model(model_id, MODEL_BINARY, architecture=MODEL_ARCHITECTURE)

    task_id, created_receipt, funded_receipt = create_funded_task(description_hash)
    task_id_hex = task_id.hex()

    # 1. the listener dispatches only once it has seen TaskCreated *and* TaskFunded
    await pipeline.listener._process_events(created_receipt.blockNumber, funded_receipt.blockNumber)
    dispatch_state = json.loads(await redis_client.get(f"event_listener:task:{task_id_hex}"))
    assert dispatch_state["dispatched"] is True

    # 2. the executor drains the queued task through every stage
    records = await pipeline.drain()
    assert len(records) == 1
    record = records[0]
    assert record.outcome == OUTCOME_SUBMITTED
    assert record.task_id == task_id_hex
    assert record.model_id == model_id
    assert record.execution_id
    assert record.verified is True
    assert record.proof_hash

    # 3. a real ExecutionReceipt was produced: the durable audit stream agrees
    audit = await redis_client.xrange("inference:audit_log")
    assert len(audit) == 1
    _entry_id, audit_entry = audit[0]
    assert audit_entry[b"execution_id"].decode() == record.execution_id
    assert audit_entry[b"model_id"].decode() == model_id

    # 4. real enclave output, not a stub: the simulator echoes its input back
    _task_id, attestation, proof, model_cid = pipeline.submitter.submissions[0]
    expected_output = b"simulated_output_for: " + b"pipeline-input:" + task_id_hex.encode()
    assert attestation.output_hash == hashlib.sha256(expected_output).hexdigest()
    assert proof.public_inputs["output_hash"] == attestation.output_hash
    assert proof.public_inputs["architecture"] == MODEL_ARCHITECTURE
    assert model_cid

    # 5. the TEE attestation verifies through the public verification interface
    tee_response = await interface.verify(
        VerificationRequest(
            proof_format="tee_attestation",
            public_inputs={
                "input_hash": attestation.input_hash,
                "output_hash": attestation.output_hash,
            },
            proof_bytes=serialize_attestation(attestation, tee.enclave.public_key_bytes),
        )
    )
    assert tee_response.result is True
    assert tee_response.reason_code is None

    # 6. the zkML proof verifies independently (this is the pipeline's stage 5)
    zkml_response = await interface.verify(
        VerificationRequest(
            proof_format="zkml_snark",
            public_inputs=proof.public_inputs,
            proof_bytes=serialize_proof_artifact(proof),
        )
    )
    assert zkml_response.result is True
    assert zkml_response.reason_code is None

    # 7. settlement reached the chain
    assert record.tx_hash is not None
    assert web3.eth.get_transaction_receipt(record.tx_hash).status == 1
    assert pipeline.submitter.metrics["proofs_submitted"] == 1
    assert pipeline.submitter.metrics["dlq_batches"] == 0
    assert pipeline.stats()["outcome_counts"] == {OUTCOME_SUBMITTED: 1}

    # 8. Step 16.9: real dispatch-to-settlement numbers for this run
    report = pipeline.latency_report()
    assert report["tasks"] == 1
    assert report["total_s"]["max"] >= report["inference_s"]["max"]
    assert report["confirmation_s"]["max"] > 0
    print("\nPIPELINE_LATENCY_REPORT " + json.dumps(report, sort_keys=True))


@pytest.mark.asyncio
async def test_dispatch_to_confirmation_latency_over_a_small_batch(
    wired: dict[str, Any], create_funded_task: FundedTaskFactory
) -> None:
    """Step 16.9: real numbers for the whole chain, settled one task at a time.

    The batch is settled serially on purpose: a flush carries every proof that is
    buffered at that moment, so only the task that triggered it has an
    attributable dispatch-to-confirmation latency. One task per flush keeps every
    sample a complete journey, which is what the design doc's budget (30s
    inference + 120s zkML + 10s batch window + confirmation) is about.
    """
    pipeline, registry = wired["pipeline"], wired["registry"]
    web3 = wired["web3"]
    description_hash = web3.keccak(text="pipeline-latency-model")
    await registry.upload_model(
        description_hash.hex(), MODEL_BINARY, architecture=MODEL_ARCHITECTURE
    )

    for _ in range(BATCH_SIZE):
        _task_id, created_receipt, funded_receipt = create_funded_task(description_hash)
        await pipeline.listener._process_events(
            created_receipt.blockNumber, funded_receipt.blockNumber
        )
        records = await pipeline.drain()
        assert [record.outcome for record in records] == [OUTCOME_SUBMITTED]

    report = pipeline.latency_report()
    assert report["tasks"] == BATCH_SIZE
    assert len({record.tx_hash for record in pipeline.records}) == BATCH_SIZE
    assert all(record.confirmation_s is not None for record in pipeline.records)
    # one task's whole journey must fit inside the inference timeout budget
    assert report["total_s"]["max"] < 30.0
    print("\nPIPELINE_BATCH_LATENCY_REPORT " + json.dumps(report, sort_keys=True))
