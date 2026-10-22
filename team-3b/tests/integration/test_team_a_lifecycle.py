"""Team A's real task lifecycle: create → fund → bid → accept → proof → settle.

These rules exist only in Team A's deployed contracts. The stubs we used to run
(in ``mocks/legacy_mock_contracts/``) mirrored the ABI and emitted the same events
but enforced none of this: no Prover allow-list, no bids, no status transitions,
no escrow release. The bytecode installed by ``scripts/ops/install_team_a_local``
is Team A's own, so the reverts and status codes below are theirs.
"""

from collections.abc import Iterator
from typing import Any

import pytest
from web3 import Web3
from web3.exceptions import ContractLogicError

from tests import chain_helper

# ``ITaskManager.TaskStatus``: 0 open, 1 worker assigned, 2 completed, 3 settled.
OPEN, ASSIGNED, COMPLETED, SETTLED = 0, 1, 2, 3


@pytest.fixture(scope="module")
def chain() -> Iterator[dict[str, Any]]:
    nodes = chain_helper.connect_or_start_node()
    web3 = next(nodes)
    try:
        yield chain_helper.install_team_a(web3) | {"web3": web3}
    finally:
        nodes.close()


def _open_funded_task(chain: dict[str, Any]) -> tuple[Web3, Any, bytes, str, str, int]:
    web3 = chain["web3"]
    creator, worker = web3.eth.accounts[0], web3.eth.accounts[3]
    bounty = int(web3.to_wei(1, "ether"))
    task_id, _created, _funded = chain_helper.create_and_fund(
        web3, chain["task_manager"], chain["escrow"], chain["token"], bounty=bounty, creator=creator
    )
    return web3, chain["task_manager"], task_id, creator, worker, bounty


def _assert_reverts_with(exc: BaseException, contract: str, error: str) -> None:
    assert chain_helper.revert_selector(exc) == chain_helper.error_selector(contract, error), str(exc)


def test_accepted_bid_then_proof_then_settlement_pays_the_worker(chain: dict[str, Any]) -> None:
    web3, task_manager, task_id, creator, worker, bounty = _open_funded_task(chain)
    escrow, settlement, token = chain["escrow"], chain["settlement"], chain["token"]

    assert task_manager.functions.getStatus(task_id).call() == OPEN
    assert task_manager.functions.taskWorker(task_id).call() == "0x" + "0" * 40

    chain_helper.place_bid(web3, task_manager, task_id, worker, price=bounty)
    bid = task_manager.functions.getBid(task_id, worker).call()
    assert bid[0] == bounty and bid[3] is True

    accepted = chain_helper.accept_bid(web3, task_manager, task_id, worker, creator)
    assert task_manager.events.BidAccepted().process_receipt(accepted)[0]["args"] == {
        "taskId": task_id,
        "worker": worker,
    }
    assert task_manager.functions.taskWorker(task_id).call() == worker
    assert task_manager.functions.getStatus(task_id).call() == ASSIGNED

    # The prover allow-listed by the installer submits, not the worker.
    proof_hash, result_hash = web3.keccak(text="proof"), web3.keccak(text="result")
    web3.eth.wait_for_transaction_receipt(
        task_manager.functions.submitProofs([task_id], [proof_hash], ["QmModel"], [result_hash]).transact(
            {"from": creator}
        )
    )
    assert task_manager.functions.getProof(task_id).call()[0] == proof_hash
    assert task_manager.functions.getStatus(task_id).call() == COMPLETED

    before = token.functions.balanceOf(worker).call()
    settled = chain_helper.settle(web3, settlement, task_id, creator)
    assert task_manager.functions.getStatus(task_id).call() == SETTLED
    assert escrow.functions.getBalance(task_id).call() == 0

    event: Any = settlement.events.TaskSettled().process_receipt(settled)[0]["args"]
    assert event["worker"] == worker
    assert event["workerAmount"] > 0
    assert token.functions.balanceOf(worker).call() - before == event["workerAmount"]


def test_settlement_without_an_accepted_worker_is_refused(chain: dict[str, Any]) -> None:
    """A proof alone does not make the bounty payable: someone must accept a bid.

    ``submitProofs`` is not gated on the bid flow, so this is exactly the state our
    submitter can produce on its own. Settlement then reverts in Team A's contract.
    """
    web3, task_manager, task_id, creator, _worker, _bounty = _open_funded_task(chain)
    escrow, settlement = chain["escrow"], chain["settlement"]

    web3.eth.wait_for_transaction_receipt(
        task_manager.functions.submitProofs(
            [task_id], [web3.keccak(text="proof")], ["QmModel"], [web3.keccak(text="result")]
        ).transact({"from": creator})
    )
    assert task_manager.functions.getStatus(task_id).call() == COMPLETED

    with pytest.raises(ContractLogicError) as error:
        chain_helper.settle(web3, settlement, task_id, creator)

    _assert_reverts_with(error.value, "SettlementContract", "NoWorkerAssigned")
    assert escrow.functions.getBalance(task_id).call() == web3.to_wei(1, "ether")


def test_only_the_creator_can_mark_a_task_complete(chain: dict[str, Any]) -> None:
    web3, task_manager, task_id, creator, worker, bounty = _open_funded_task(chain)
    chain_helper.place_bid(web3, task_manager, task_id, worker, price=bounty)
    chain_helper.accept_bid(web3, task_manager, task_id, worker, creator)

    with pytest.raises(ContractLogicError) as error:
        chain_helper.mark_complete(web3, task_manager, task_id, worker)
    _assert_reverts_with(error.value, "TaskManager", "NotTaskCreator")

    chain_helper.mark_complete(web3, task_manager, task_id, creator)
    assert task_manager.functions.getStatus(task_id).call() == COMPLETED


def test_submit_proofs_is_gated_by_the_prover_allowlist(chain: dict[str, Any]) -> None:
    web3, task_manager, task_id, _creator, _worker, _bounty = _open_funded_task(chain)
    outsider = list(web3.eth.accounts)[4]
    assert task_manager.functions.isProver(outsider).call() is False

    with pytest.raises(ContractLogicError) as error:
        task_manager.functions.submitProofs(
            [task_id], [web3.keccak(text="proof")], ["QmModel"], [web3.keccak(text="result")]
        ).transact({"from": outsider})

    _assert_reverts_with(error.value, "TaskManager", "NotProver")
    assert task_manager.functions.getStatus(task_id).call() == OPEN
