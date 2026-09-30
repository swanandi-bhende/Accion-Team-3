import json
import subprocess
import time
from pathlib import Path

import fakeredis.aioredis as fakeredis
import pytest
from web3 import Web3

from common.models import AttestationReport, ProofArtifact
from event_listener.listener import EventListener
from proof_submitter import ProofSubmitter


ROOT = Path(__file__).resolve().parents[2]
CONTRACTS = ROOT / "mocks" / "contracts"


@pytest.fixture(scope="module")
def mock_pipeline_chain():
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
            pytest.fail("Hardhat node did not start")
    web3.eth.default_account = web3.eth.accounts[0]
    contracts = {}
    for name in ("TaskManager", "Escrow"):
        artifact = json.loads((CONTRACTS / f"artifacts/contracts/{name}.sol/{name}.json").read_text())
        deployment = web3.eth.wait_for_transaction_receipt(
            web3.eth.contract(abi=artifact["abi"], bytecode=artifact["bytecode"]).constructor().transact()
        )
        contracts[name] = web3.eth.contract(address=deployment.contractAddress, abi=artifact["abi"])
    yield web3, contracts["TaskManager"], contracts["Escrow"]
    if process is not None and process.poll() is None:
        process.terminate()
        process.wait(timeout=10)


@pytest.mark.asyncio
async def test_event_listener_to_proof_submitter_pipeline(mock_pipeline_chain):
    web3, task_manager, escrow = mock_pipeline_chain
    redis = fakeredis.FakeRedis(decode_responses=False)
    description_hash = web3.keccak(text="pipeline-model")
    bounty = web3.to_wei(1, "ether")
    deadline = int(time.time()) + 3600
    created_receipt = web3.eth.wait_for_transaction_receipt(
        task_manager.functions.createTask(description_hash, deadline, bounty).transact()
    )
    task_id = task_manager.events.TaskCreated().process_receipt(created_receipt)[0]["args"]["taskId"]
    funded_receipt = web3.eth.wait_for_transaction_receipt(
        escrow.functions.fundTask(task_id).transact({"value": bounty})
    )
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
    if submitter._batch_timer is not None:
        submitter._batch_timer.cancel()
    proofs = list(submitter._pending_proofs)
    submitter._pending_proofs.clear()
    tx_hash = await submitter._send_transaction(proofs)

    assert len(proofs) == 1
    assert web3.eth.get_transaction_receipt(tx_hash).status == 1