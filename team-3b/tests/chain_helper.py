"""Shared helpers for tests that need a chain running Team A's real contracts.

The contracts are not compiled from Solidity (Team A ship ABIs and deployed
addresses, not source). Their runtime bytecode is replayed onto a local Hardhat
node with ``hardhat_setCode`` by ``scripts/ops/install_team_a_local`` and wired
the same way Team A wired Galileo, so tests exercise the real authorisation and
lifecycle rules rather than a stand-in.
"""

from __future__ import annotations

import subprocess
import time
from collections.abc import Generator
from pathlib import Path
from typing import Any, cast

from web3 import Web3

ROOT = Path(__file__).resolve().parents[1]
CHAIN_PROJECT = ROOT / "chain"
RPC_URL = "http://127.0.0.1:8545"
NODE_START_TIMEOUT_S = 60.0


def connect_or_start_node() -> Generator[Web3, None, None]:
    """Yield a Web3 for a node on 8545, starting one from chain/ when absent."""
    web3 = Web3(Web3.HTTPProvider(RPC_URL))
    process = None
    if not web3.is_connected():
        if not (CHAIN_PROJECT / "node_modules").exists():
            subprocess.run(["npm", "install"], cwd=CHAIN_PROJECT, check=True, capture_output=True, text=True)
        process = subprocess.Popen(
            ["npx", "hardhat", "node", "--hostname", "127.0.0.1", "--port", "8545"],
            cwd=CHAIN_PROJECT,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.STDOUT,
        )
        deadline = time.monotonic() + NODE_START_TIMEOUT_S
        while time.monotonic() < deadline and not web3.is_connected():
            time.sleep(0.25)
        if not web3.is_connected():
            if process.poll() is None:
                process.terminate()
            raise RuntimeError("Hardhat node did not start within 60s")
    web3.eth.default_account = web3.eth.accounts[0]
    try:
        yield web3
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            process.wait(timeout=10)


def install_team_a(web3: Web3) -> dict[str, Any]:
    """Install Team A's bytecode on the node; returns the wired contracts."""
    from scripts.ops.install_team_a_local import install

    install(web3)
    deployment = {
        "task_manager": contract(web3, "TaskManager"),
        "escrow": contract(web3, "EscrowContract"),
        "settlement": contract(web3, "SettlementContract"),
        "token": token_contract(web3),
        "funder": funder_account(web3),
    }
    return deployment


def _addresses() -> dict[str, str]:
    import json

    return cast(dict[str, str], json.loads((ROOT / "deployments" / "galileo.json").read_text(encoding="utf-8")))


def _abi(name: str) -> list[dict[str, Any]]:
    import json

    return cast(
        list[dict[str, Any]],
        json.loads((ROOT / "team_a_assets" / f"{name}.abi.json").read_text(encoding="utf-8")),
    )


# The TEST token's ABI was not part of Team A's handoff, so the ERC-20 surface
# tests need is spelled out here rather than read from an export.
TOKEN_ABI = [
    {
        "type": "function",
        "name": name,
        "stateMutability": state,
        "inputs": inputs,
        "outputs": outputs,
    }
    for name, state, inputs, outputs in [
        ("approve", "nonpayable", [{"name": "spender", "type": "address"}, {"name": "amount", "type": "uint256"}], [{"name": "", "type": "bool"}]),
        ("balanceOf", "view", [{"name": "account", "type": "address"}], [{"name": "", "type": "uint256"}]),
    ]
]


ADDRESS_KEYS = {
    "TaskManager": "TASK_MANAGER_ADDRESS",
    "EscrowContract": "ESCROW_ADDRESS",
    "SettlementContract": "SETTLEMENT_ADDRESS",
    "StakingContract": "STAKING_ADDRESS",
    "IdentityContract": "IDENTITY_ADDRESS",
    "TEST_TOKEN": "BOUNTY_TOKEN_ADDRESS",
}


def contract(web3: Web3, name: str) -> Any:
    return web3.eth.contract(address=web3.to_checksum_address(_addresses()[ADDRESS_KEYS[name]]), abi=_abi(name))


def token_contract(web3: Web3) -> Any:
    return web3.eth.contract(address=web3.to_checksum_address(_addresses()["BOUNTY_TOKEN_ADDRESS"]), abi=TOKEN_ABI)


def funder_account(web3: Web3) -> str:
    """The account install_team_a_local funds with the TEST token."""
    accounts = list(web3.eth.accounts)
    return str(accounts[2] if len(accounts) > 2 else accounts[0])


def create_and_fund(
    web3: Web3,
    task_manager: Any,
    escrow: Any,
    token: Any,
    description_hash: bytes | None = None,
    bounty: int = 10**18,
    creator: str | None = None,
) -> tuple[Any, Any, Any]:
    """Create a task and fund it through Team A's ERC-20 escrow deposit."""
    import time as _time

    funder = funder_account(web3)
    deadline = int(_time.time()) + 3600
    created = web3.eth.wait_for_transaction_receipt(
        task_manager.functions.createTask(
            description_hash or web3.keccak(text="test-model"), deadline, bounty
        ).transact({"from": creator} if creator else {})
    )
    task_id = task_manager.events.TaskCreated().process_receipt(created)[0]["args"]["taskId"]
    web3.eth.wait_for_transaction_receipt(token.functions.approve(escrow.address, bounty).transact({"from": funder}))
    funded = web3.eth.wait_for_transaction_receipt(escrow.functions.deposit(task_id, bounty).transact({"from": funder}))
    return task_id, created, funded


# --- Team A's bid / completion / settlement lifecycle -------------------------


def error_selector(contract_name: str, error_name: str) -> str:
    """The 4-byte selector of one of Team A's custom errors.

    Web3 does not decode these against our exported ABIs (the node reports the
    raw selector), so tests compare selectors instead of message text.
    """
    from eth_utils import keccak

    abi = _abi(contract_name)
    error = next(
        item for item in abi if item.get("type") == "error" and item.get("name") == error_name
    )
    signature = f"{error_name}({','.join(item['type'] for item in error['inputs'])})"
    return "0x" + keccak(text=signature)[:4].hex()


def revert_selector(exc: BaseException) -> str:
    """The raw revert selector web3 attaches to a ``ContractLogicError``."""
    data = getattr(exc, "data", None)
    if isinstance(data, dict):
        data = data.get("data")
    return str(data or "")


def place_bid(
    web3: Web3,
    task_manager: Any,
    task_id: bytes,
    worker: str,
    price: int = 10**18,
    eta_seconds: int = 3600,
) -> Any:
    """A worker bids on the task (``BidPlaced``); allowed while the task is open."""
    return web3.eth.wait_for_transaction_receipt(
        task_manager.functions.placeBid(
            task_id, price, eta_seconds, web3.keccak(text="capability")
        ).transact({"from": worker})
    )


def accept_bid(web3: Web3, task_manager: Any, task_id: bytes, worker: str, creator: str) -> Any:
    """The creator accepts a bid (``BidAccepted``), assigning ``taskWorker``."""
    return web3.eth.wait_for_transaction_receipt(
        task_manager.functions.acceptBid(task_id, worker).transact({"from": creator})
    )


def mark_complete(web3: Web3, task_manager: Any, task_id: bytes, creator: str) -> Any:
    """The creator (only) moves an assigned task to Completed."""
    return web3.eth.wait_for_transaction_receipt(
        task_manager.functions.markComplete(task_id).transact({"from": creator})
    )


def settle(web3: Web3, settlement: Any, task_id: bytes, caller: str) -> Any:
    """SettlementContract pays the assigned worker out of escrow."""
    return web3.eth.wait_for_transaction_receipt(
        settlement.functions.settleTask(task_id).transact({"from": caller})
    )
