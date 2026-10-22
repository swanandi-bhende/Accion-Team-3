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
import os
import time

from web3 import Web3
from web3.logs import IGNORE

from scripts.ops import team_a


def trigger(
    web3: Web3,
    description: str = "linear-model",
    bounty: int = 10**18,
    deadline_in: int = 3600,
) -> str:
    """Create a task and fund it; returns the ``bytes32`` task id as hex."""
    addresses = team_a.load_addresses()
    task_manager = team_a.contract(web3, "TaskManager", addresses)
    escrow = team_a.contract(web3, "EscrowContract", addresses)
    token = team_a.token_contract(web3, addresses)

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
    rpc_url = os.environ.get("WEB3_RPC_URL") or team_a.DEFAULT_RPC_URL
    web3 = Web3(Web3.HTTPProvider(rpc_url))
    if not web3.is_connected():
        raise SystemExit(f"no chain at {rpc_url}")
    description = os.environ.get("TASK_DESCRIPTION", "linear-model")
    task_id = trigger(web3, description)
    print(f"TASK_CREATED task_id={task_id}")


if __name__ == "__main__":
    main()
