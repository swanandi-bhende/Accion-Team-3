"""Create and fund one task on a local chain running Team A's contracts.

Replaces the old mock trigger. Funding follows Team A's real flow: the bounty is
their ERC-20, so the funder approves the escrow and calls ``deposit`` — the path
that emits ``TaskFunded(taskId, funder, amount)`` on Galileo.

Usage::

    python -m scripts.ops.trigger_team_a_task            # create + fund one task
    TASK_DESCRIPTION=linear-model python -m scripts.ops.trigger_team_a_task

Prints ``TASK_CREATED task_id=0x...`` so scripts and tests can parse it.
"""

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

from web3 import Web3
from web3.logs import IGNORE

ROOT = Path(__file__).resolve().parents[2]
DEPLOYMENTS = ROOT / "deployments" / "galileo.json"
ABIS = ROOT / "team_a_assets"
DEFAULT_RPC = "http://127.0.0.1:8545"

# The TEST token's ABI was not part of the handoff, so the ERC-20 surface the
# trigger needs is spelled out here rather than read from an export.
TOKEN_ABI = json.loads(
    '[{"type":"function","name":"approve","stateMutability":"nonpayable",'
    '"inputs":[{"name":"spender","type":"address"},{"name":"amount","type":"uint256"}],'
    '"outputs":[{"name":"","type":"bool"}]},'
    '{"type":"function","name":"balanceOf","stateMutability":"view",'
    '"inputs":[{"name":"account","type":"address"}],"outputs":[{"name":"","type":"uint256"}]}]'
)


def contract(web3: Web3, name: str, address: str) -> Any:
    abi = json.loads((ABIS / f"{name}.abi.json").read_text(encoding="utf-8"))
    return web3.eth.contract(address=web3.to_checksum_address(address), abi=abi)


def trigger(
    web3: Web3,
    description: str = "linear-model",
    bounty: int = 10**18,
    deadline_in: int = 3600,
) -> str:
    """Create a task and fund it; returns the ``bytes32`` task id as hex."""
    addresses = json.loads(DEPLOYMENTS.read_text(encoding="utf-8"))
    task_manager = contract(web3, "TaskManager", addresses["TASK_MANAGER_ADDRESS"])
    escrow = contract(web3, "EscrowContract", addresses["ESCROW_ADDRESS"])
    token_address = addresses.get("BOUNTY_TOKEN_ADDRESS") or addresses["TEST_TOKEN_ADDRESS"]
    token = web3.eth.contract(address=web3.to_checksum_address(token_address), abi=TOKEN_ABI)

    accounts = list(web3.eth.accounts)
    creator = accounts[0]
    funder = accounts[2] if len(accounts) > 2 else creator

    created = web3.eth.wait_for_transaction_receipt(
        task_manager.functions.createTask(  # type: ignore[attr-defined]
            hashlib.sha256(description.encode()).digest(),
            int(time.time()) + deadline_in,
            bounty,
        ).transact({"from": creator})
    )
    event = task_manager.events.TaskCreated().process_receipt(created)[0]["args"]  # type: ignore[attr-defined]
    task_id = event["taskId"]

    web3.eth.wait_for_transaction_receipt(
        token.functions.approve(escrow.address, bounty).transact({"from": funder})  # type: ignore[attr-defined]
    )
    funded = web3.eth.wait_for_transaction_receipt(
        escrow.functions.deposit(task_id, bounty).transact({"from": funder})  # type: ignore[attr-defined]
    )
    args = next(
        entry["args"]
        for entry in escrow.events.TaskFunded().process_receipt(funded, errors=IGNORE)
        # The deposit receipt also carries the token's Transfer log.
        if entry["address"] == escrow.address
    )

    task_id_hex = "0x" + str(task_id.hex()).removeprefix("0x")
    print(f"TaskCreated task_id={task_id_hex} description={description}")
    print(f"TaskFunded task_id={task_id_hex} amount={args['amount']} funder={args['funder']}")
    return task_id_hex


def main() -> None:
    rpc_url = os.environ.get("WEB3_RPC_URL") or DEFAULT_RPC
    web3 = Web3(Web3.HTTPProvider(rpc_url))
    if not web3.is_connected():
        raise SystemExit(f"no chain at {rpc_url}")
    description = os.environ.get("TASK_DESCRIPTION", "linear-model")
    task_id = trigger(web3, description)
    print(f"TASK_CREATED task_id={task_id}")


if __name__ == "__main__":
    main()
