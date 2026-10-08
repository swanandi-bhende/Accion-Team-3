import json
import subprocess
import time
from pathlib import Path

import fakeredis.aioredis as fakeredis
import pytest
from web3 import Web3

from common.models import AttestationReport, ProofArtifact
from proof_submitter import ProofSubmitter

ROOT = Path(__file__).resolve().parents[2]
CONTRACTS = ROOT / "mocks" / "contracts"


@pytest.fixture(scope="module")
def deployed_task_manager():
    web3 = Web3(Web3.HTTPProvider("http://127.0.0.1:8545"))
    process = None
    if not web3.is_connected():
        process = subprocess.Popen(
            ["npx", "hardhat", "node", "--hostname", "127.0.0.1", "--port", "8545"],
            cwd=CONTRACTS,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.STDOUT,
        )
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and not web3.is_connected():
            time.sleep(0.5)
        if not web3.is_connected():
            if process.poll() is None:
                process.terminate()
            pytest.fail("Hardhat node did not start")
    web3.eth.default_account = web3.eth.accounts[0]
    artifact = json.loads((CONTRACTS / "artifacts/contracts/TaskManager.sol/TaskManager.json").read_text())
    contract = web3.eth.contract(abi=artifact["abi"], bytecode=artifact["bytecode"])
    receipt = web3.eth.wait_for_transaction_receipt(contract.constructor().transact())
    instance = web3.eth.contract(address=receipt.contractAddress, abi=artifact["abi"])
    yield web3, instance
    if process is not None and process.poll() is None:
        process.terminate()
        process.wait(timeout=10)


@pytest.mark.asyncio
async def test_submitter_sends_real_batched_submit_proofs(deployed_task_manager):
    web3, contract = deployed_task_manager
    submitter = ProofSubmitter(
        web3,
        contract,
        fakeredis.FakeRedis(decode_responses=False),
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
            ProofArtifact(proof_bytes=f"proof-{index}".encode(), public_inputs={"output_hash": "output"}, verification_key_ref="vk"),
            "QmModel",
        )
        for index in range(3)
    ]
    submitter._pending_proofs.clear()
    submitter._batch_timer.cancel()

    tx_hash = await submitter._send_transaction(proofs)

    receipt = web3.eth.get_transaction_receipt(tx_hash)
    assert receipt.status == 1
    assert submitter.metrics["proofs_submitted"] == 0
