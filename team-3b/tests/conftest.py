"""Shared test fixtures.

The mock chain is owned here rather than duplicated per integration module: one
Hardhat node per session (so node startup is paid once and teardown cannot race
another module's), with a fresh ``TaskManager``/``Escrow`` deployment per module.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import time
from collections.abc import Callable, Generator
from pathlib import Path
from typing import Any, cast

import pytest
from web3 import Web3
from web3.contract import Contract
from web3.types import TxReceipt

from common.interfaces import StorageBackend

ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = ROOT / "mocks" / "contracts"
RPC_URL = "http://127.0.0.1:8545"
NODE_START_TIMEOUT_S = 60.0
CONTRACT_NAMES = ("TaskManager", "Escrow")


class InMemoryStorage(StorageBackend):
    """Content-addressed storage double, so tests need no IPFS node.

    ``tamper()`` makes every later ``retrieve`` return bytes that differ from
    what was uploaded, which is how the integrity-check failure is injected
    after a model has registered successfully.
    """

    def __init__(self) -> None:
        self.store: dict[str, bytes] = {}
        self.corrupt = False

    async def upload(self, data: bytes) -> str:
        cid = "Qm" + hashlib.sha256(data).hexdigest()
        self.store[cid] = data
        return cid

    async def retrieve(self, cid: str) -> bytes:
        if self.corrupt:
            return b"tampered-bytes-that-do-not-match-the-committed-hash"
        return self.store[cid]

    def tamper(self) -> None:
        self.corrupt = True


@pytest.fixture
def memory_storage() -> InMemoryStorage:
    return InMemoryStorage()


def _deploy(web3: Web3, name: str) -> Contract:
    artifact = json.loads((CONTRACTS / f"artifacts/contracts/{name}.sol/{name}.json").read_text())
    deployment = web3.eth.wait_for_transaction_receipt(
        web3.eth.contract(abi=artifact["abi"], bytecode=artifact["bytecode"]).constructor().transact()
    )
    return cast(Contract, web3.eth.contract(address=deployment["contractAddress"], abi=artifact["abi"]))


@pytest.fixture(scope="session")
def hardhat_node() -> Generator[Web3, None, None]:
    """A local Hardhat node, reusing one already listening on 8545."""
    web3 = Web3(Web3.HTTPProvider(RPC_URL))
    process = None
    if not web3.is_connected():
        process = subprocess.Popen(
            ["npx", "hardhat", "node", "--hostname", "127.0.0.1", "--port", "8545"],
            cwd=CONTRACTS,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.STDOUT,
        )
        deadline = time.monotonic() + NODE_START_TIMEOUT_S
        while time.monotonic() < deadline and not web3.is_connected():
            time.sleep(0.25)
        if not web3.is_connected():
            process.terminate()
            pytest.fail("Hardhat node did not start within 60s")
    web3.eth.default_account = web3.eth.accounts[0]
    yield web3
    if process is not None and process.poll() is None:
        process.terminate()
        process.wait(timeout=10)


@pytest.fixture(scope="module")
def mock_chain(hardhat_node: Web3) -> tuple[Web3, Contract, Contract]:
    """``(web3, task_manager, escrow)`` with freshly deployed mock contracts."""
    task_manager, escrow = (_deploy(hardhat_node, name) for name in CONTRACT_NAMES)
    return hardhat_node, task_manager, escrow


@pytest.fixture
def create_funded_task(mock_chain: tuple[Web3, Contract, Contract]) -> Callable[..., tuple[Any, TxReceipt, TxReceipt]]:
    """Callable that posts ``TaskCreated`` then ``TaskFunded`` for one task."""
    web3, task_manager, escrow = mock_chain

    def create_and_fund(description_hash: bytes, bounty_ether: int = 1) -> tuple[Any, TxReceipt, TxReceipt]:
        bounty = web3.to_wei(bounty_ether, "ether")
        deadline = int(time.time()) + 3600
        created = web3.eth.wait_for_transaction_receipt(
            task_manager.functions.createTask(description_hash, deadline, bounty).transact()
        )
        task_id = task_manager.events.TaskCreated().process_receipt(created)[0]["args"]["taskId"]
        funded = web3.eth.wait_for_transaction_receipt(
            escrow.functions.fundTask(task_id).transact({"value": bounty})
        )
        return task_id, created, funded

    return create_and_fund
