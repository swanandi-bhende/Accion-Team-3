"""TaskCreated / TaskFunded / submitProofs round trip on Team A's real contracts.

The contracts are Team A's deployed bytecode, installed on a local Hardhat node
by scripts/ops/install_team_a_local (see tests/chain_helper.py), so this covers
the real ERC-20 escrow path and the real Prover allow-list.
"""

from collections.abc import Iterator
from typing import Any

import pytest
from web3 import Web3
from web3.exceptions import Web3Exception
from web3.logs import IGNORE

from tests import chain_helper


@pytest.fixture(scope="module")
def chain() -> Iterator[tuple[Web3, Any, Any, Any]]:
    nodes = chain_helper.connect_or_start_node()
    web3 = next(nodes)
    deployment = chain_helper.install_team_a(web3)
    try:
        yield web3, deployment["task_manager"], deployment["escrow"], deployment["token"]
    finally:
        nodes.close()


def test_chain_events_and_submit(chain: tuple[Web3, Any, Any, Any]) -> None:
    web3, task_manager, escrow, token = chain
    assert web3.is_connected()

    account = web3.eth.accounts[0]
    funder = chain_helper.funder_account(web3)
    w3 = web3
    w3.eth.default_account = account

    # 1. TaskCreated
    desc_hash = w3.keccak(text="team_a_description")
    deadline = 9999999999
    bounty = w3.to_wei(1, "ether")
    receipt: Any = w3.eth.wait_for_transaction_receipt(
        task_manager.functions.createTask(desc_hash, deadline, bounty).transact()
    )
    events = task_manager.events.TaskCreated().process_receipt(receipt)
    assert len(events) == 1
    task_id = events[0]["args"]["taskId"]
    assert events[0]["args"]["descriptionHash"] == desc_hash

    # 2. TaskFunded, through Team A's ERC-20 escrow deposit
    w3.eth.wait_for_transaction_receipt(
        token.functions.approve(escrow.address, bounty).transact({"from": funder})
    )
    receipt = w3.eth.wait_for_transaction_receipt(
        escrow.functions.deposit(task_id, bounty).transact({"from": funder})
    )
    events = [
        entry
        for entry in escrow.events.TaskFunded().process_receipt(receipt, errors=IGNORE)
        # The deposit receipt also carries the token's Transfer log.
        if entry["address"] == escrow.address
    ]
    assert len(events) == 1
    assert events[0]["args"]["taskId"] == task_id
    assert events[0]["args"]["amount"] == bounty
    assert events[0]["args"]["funder"] == funder
    assert escrow.functions.getBalance(task_id).call() == bounty

    # 3. submitProofs from the allow-listed prover
    proof_hash = w3.keccak(text="proof")
    model_cid = "Qm123"
    result_hash = w3.keccak(text="result")

    receipt = w3.eth.wait_for_transaction_receipt(
        task_manager.functions.submitProofs([task_id], [proof_hash], [model_cid], [result_hash]).transact()
    )
    assert receipt.status == 1  # Success
    proof_record = task_manager.functions.getProof(task_id).call()
    assert proof_record[0] == proof_hash
    assert proof_record[1] == model_cid
    assert proof_record[2] == result_hash

    # 4. A signer that is not on the allow-list is rejected
    other = list(w3.eth.accounts)[3]
    with pytest.raises(Web3Exception):
        w3.eth.wait_for_transaction_receipt(
            task_manager.functions.submitProofs([task_id], [proof_hash], [model_cid], [result_hash]).transact(
                {"from": other}
            )
        )
