import json
import subprocess
import time
from pathlib import Path

import fakeredis.aioredis as fakeredis
import pytest
from web3 import Web3

from event_listener.listener import EventListener

ROOT = Path(__file__).resolve().parents[2]
CONTRACTS = ROOT / "mocks" / "contracts"


def artifact(name):
    path = CONTRACTS / "artifacts" / "contracts" / f"{name}.sol" / f"{name}.json"
    data = json.loads(path.read_text())
    return data["abi"], data["bytecode"]


@pytest.fixture(scope="module")
def mock_chain():
    provider = Web3.HTTPProvider("http://127.0.0.1:8545")
    web3 = Web3(provider)
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
    tm_abi, tm_bytecode = artifact("TaskManager")
    escrow_abi, escrow_bytecode = artifact("Escrow")
    tm_receipt = web3.eth.wait_for_transaction_receipt(
        web3.eth.contract(abi=tm_abi, bytecode=tm_bytecode).constructor().transact()
    )
    escrow_receipt = web3.eth.wait_for_transaction_receipt(
        web3.eth.contract(abi=escrow_abi, bytecode=escrow_bytecode).constructor().transact()
    )
    yield web3, web3.eth.contract(address=tm_receipt.contractAddress, abi=tm_abi), web3.eth.contract(
        address=escrow_receipt.contractAddress, abi=escrow_abi
    )

    if process is not None and process.poll() is None:
        process.terminate()
        process.wait(timeout=10)


@pytest.mark.asyncio
async def test_listener_processes_real_mock_chain_events(mock_chain):
    web3, task_manager, escrow = mock_chain
    description_hash = web3.keccak(text="linear-model")
    deadline = int(time.time()) + 3600
    bounty = web3.to_wei(1, "ether")
    created_receipt = web3.eth.wait_for_transaction_receipt(
        task_manager.functions.createTask(description_hash, deadline, bounty).transact()
    )
    created_event = task_manager.events.TaskCreated().process_receipt(created_receipt)[0]["args"]
    task_id = created_event["taskId"]
    funded_receipt = web3.eth.wait_for_transaction_receipt(
        escrow.functions.fundTask(task_id).transact({"value": bounty})
    )
    dispatches = []
    listener = EventListener(
        web3=web3,
        task_manager=task_manager,
        escrow=escrow,
        redis_client=fakeredis.FakeRedis(decode_responses=False),
        dispatch=lambda task: dispatches.append(task),
        clock=lambda: float(int(time.time())),
    )

    await listener._process_events(created_receipt.blockNumber, funded_receipt.blockNumber)

    assert task_id.hex() in listener._task_events
    assert len(dispatches) == 1
    assert dispatches[0]["created"]["description_hash"] == description_hash
    assert dispatches[0]["funded"]["funded_amount"] == bounty
