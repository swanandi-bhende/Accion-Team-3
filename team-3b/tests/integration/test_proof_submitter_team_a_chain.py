import hashlib
from collections.abc import Callable, Generator
from typing import Any, cast

import fakeredis.aioredis as fakeredis
import pytest
from eth_typing import HexStr
from web3 import Web3

from common.constants import DEVELOPMENT_PROOF_BACKEND
from common.models import AttestationReport, ProofArtifact
from proof_submitter import ProofSubmitter
from proof_submitter.submitter import PendingProof
from tests import chain_helper


@pytest.fixture(scope="module")
def deployed_task_manager() -> Generator[tuple[Web3, Any], None, None]:
    """Team A's TaskManager, installed on the local node with its real rules."""
    nodes = chain_helper.connect_or_start_node()
    web3 = next(nodes)
    deployment = chain_helper.install_team_a(web3)
    try:
        yield web3, deployment["task_manager"]
    finally:
        nodes.close()


@pytest.mark.asyncio
async def test_submitter_sends_real_batched_submit_proofs(deployed_task_manager: tuple[Web3, Any]) -> None:
    web3, contract = deployed_task_manager
    submitter = ProofSubmitter(
        web3,
        contract,
        fakeredis.FakeRedis(decode_responses=False),
        allow_development_backend=True,
    )
    attestation = AttestationReport(
        enclave_measurement="measurement",
        signer_identity="signer",
        input_hash="input",
        output_hash="output",
        hardware_signature=b"signature",
        timestamp=1.0,
    )
    proofs = [
        await submitter.submit_proof(
            f"task-{index}",
            attestation,
            ProofArtifact(
                proof_bytes=f"proof-{index}".encode(),
                public_inputs={"output_hash": "output"},
                verification_key_ref="vk",
                backend=DEVELOPMENT_PROOF_BACKEND,
            ),
            "QmModel",
        )
        for index in range(3)
    ]
    submitter._pending_proofs.clear()
    assert submitter._batch_timer is not None
    submitter._batch_timer.cancel()

    tx_hash = await submitter._send_transaction(proofs)

    receipt: Any = web3.eth.get_transaction_receipt(cast(HexStr, tx_hash))
    assert receipt.status == 1
    assert submitter.metrics["proofs_submitted"] == 0


@pytest.mark.asyncio
async def test_resubmitting_an_accepted_proof_neither_double_records_nor_dead_letters(
    team_a_chain: tuple[Web3, Any, Any],
    create_funded_task: Callable[..., Any],
) -> None:
    """Team A's contract rejects a duplicate ProofRejected, so the submitter must not resend it."""
    web3, task_manager, _escrow = team_a_chain
    task_id, _created, _funded = create_funded_task(hashlib.sha256(b"resubmit-proof").digest())
    redis_client = fakeredis.FakeRedis(decode_responses=False)
    submitter = ProofSubmitter(web3, task_manager, redis_client, allow_development_backend=True)
    attestation = AttestationReport(
        enclave_measurement="measurement",
        signer_identity="signer",
        input_hash="input",
        output_hash="output",
        hardware_signature=b"signature",
        timestamp=1.0,
    )
    proof = PendingProof(
        "0x" + bytes(task_id).hex(),
        attestation,
        ProofArtifact(
            proof_bytes=b"proof-resubmit",
            public_inputs={"output_hash": "output"},
            verification_key_ref="vk",
            backend=DEVELOPMENT_PROOF_BACKEND,
        ),
        "QmResubmit",
        "11" * 32,
        "22" * 32,
    )

    assert await submitter._submit_batch([proof]) is not None
    recorded = task_manager.functions.getProof(bytes(task_id)).call()

    assert await submitter._submit_batch([proof]) is None
    assert task_manager.functions.getProof(bytes(task_id)).call() == recorded
    assert await submitter.redis.llen(ProofSubmitter.DLQ_KEY) == 0
