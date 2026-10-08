"""Mock-chain end-to-end: event listener -> proof submitter -> on-chain proof.

The chain bootstrap lives in ``tests/conftest.py`` so this test and the full
pipeline test share one Hardhat session.
"""

import time

import fakeredis.aioredis as fakeredis
import pytest

from common.models import AttestationReport, ProofArtifact
from event_listener.listener import EventListener
from proof_submitter import ProofSubmitter


@pytest.mark.asyncio
async def test_event_listener_to_proof_submitter_pipeline(mock_chain, create_funded_task):
    web3, task_manager, escrow = mock_chain
    redis = fakeredis.FakeRedis(decode_responses=False)
    description_hash = web3.keccak(text="pipeline-model")
    task_id, created_receipt, funded_receipt = create_funded_task(description_hash)
    submitter = ProofSubmitter(web3, task_manager, redis, batch_window=10.0)

    async def dispatch(task):
        attestation = AttestationReport(
            enclave_measurement="mock-measurement",
            signer_identity="mock-signer",
            input_hash="input-hash",
            output_hash="output-hash",
            hardware_signature=b"attestation-signature",
            timestamp=time.time(),
        )
        proof = ProofArtifact(
            proof_bytes=b"mock-zkml-proof",
            public_inputs={"output_hash": "output-hash"},
            verification_key_ref="mock-vk",
        )
        await submitter.submit_proof(task["created"]["task_id"], attestation, proof, "QmPipelineModel")

    listener = EventListener(
        web3,
        task_manager,
        escrow,
        redis,
        dispatch=dispatch,
        clock=lambda: float(int(time.time())),
    )
    await listener._process_events(created_receipt.blockNumber, funded_receipt.blockNumber)

    assert submitter.buffered_task_ids() == [task_id.hex()]

    tx_hash = await submitter.flush()

    assert submitter.buffered_task_ids() == []
    assert submitter.metrics["proofs_submitted"] == 1
    assert submitter.metrics["dlq_batches"] == 0
    assert web3.eth.get_transaction_receipt(tx_hash).status == 1
    assert await submitter.flush() is None
