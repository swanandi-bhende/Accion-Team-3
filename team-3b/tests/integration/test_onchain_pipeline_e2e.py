"""Mock-chain end-to-end: event listener -> proof submitter -> on-chain proof.

The chain bootstrap lives in ``tests/conftest.py`` so this test and the full
pipeline test share one Hardhat session.
"""

import time
from collections.abc import Callable
from typing import Any, cast

import fakeredis.aioredis as fakeredis
import pytest
from eth_typing import HexStr
from web3 import Web3

from common.constants import DEVELOPMENT_PROOF_BACKEND
from common.models import AttestationReport, ProofArtifact
from event_listener.listener import EventListener
from proof_submitter import ProofSubmitter


@pytest.mark.asyncio
async def test_event_listener_to_proof_submitter_pipeline(
    team_a_chain: tuple[Web3, Any, Any],
    create_funded_task: Callable[[bytes], tuple[Any, Any, Any]],
) -> None:
    web3, task_manager, escrow = team_a_chain
    redis = fakeredis.FakeRedis(decode_responses=False)
    description_hash = web3.keccak(text="pipeline-model")
    task_id, created_receipt, funded_receipt = create_funded_task(description_hash)
    # The proof below is a development stand-in, so this test opts in to it explicitly.
    submitter = ProofSubmitter(web3, task_manager, redis, batch_window=10.0, allow_development_backend=True)

    async def dispatch(task: dict[str, Any]) -> None:
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
            backend=DEVELOPMENT_PROOF_BACKEND,
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
    assert tx_hash is not None
    assert web3.eth.get_transaction_receipt(cast(HexStr, tx_hash))["status"] == 1
    assert await submitter.flush() is None
