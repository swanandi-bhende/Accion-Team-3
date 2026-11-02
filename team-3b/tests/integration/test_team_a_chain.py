"""Team A's real contracts on the local chain: events, the task lifecycle, and the listener and submitter against them."""

import hashlib
from collections.abc import Callable, Generator, Iterator
from typing import Any, cast

import fakeredis.aioredis as fakeredis
import pytest
from eth_typing import HexStr
from web3 import Web3
from web3.exceptions import ContractLogicError, Web3Exception
from web3.logs import IGNORE

from common.constants import DEVELOPMENT_PROOF_BACKEND
from common.models import AttestationReport, ProofArtifact
from event_listener.listener import EventListener
from proof_submitter import ProofSubmitter
from proof_submitter.submitter import PendingProof
from tests import chain_helper


@pytest.mark.asyncio
async def test_listener_processes_real_team_a_chain_events(
    team_a_chain: tuple[Web3, Any, Any],
) -> None:
    web3, task_manager, escrow = team_a_chain
    token = chain_helper.token_contract(web3)

    task_id, _created, _funded = chain_helper.create_and_fund(
        web3, task_manager, escrow, token
    )

    dispatches: list[Any] = []
    listener = EventListener(
        web3=web3,
        task_manager=task_manager,
        escrow=escrow,
        redis_client=fakeredis.FakeRedis(decode_responses=False),
        dispatch=lambda task: dispatches.append(task),
    )

    # Scan only this task's block range; the node is shared with other tests.
    await listener._process_events(_created["blockNumber"], _funded["blockNumber"])

    assert task_id.hex() in listener._task_events
    assert len(dispatches) == 1
    assert dispatches[0]["created"]["task_id"] == task_id.hex()
    assert dispatches[0]["funded"]["funded_amount"] == web3.to_wei(1, "ether")


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
async def test_submitter_sends_real_batched_submit_proofs(
    deployed_task_manager: tuple[Web3, Any],
) -> None:
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
    task_id, _created, _funded = create_funded_task(
        hashlib.sha256(b"resubmit-proof").digest()
    )
    redis_client = fakeredis.FakeRedis(decode_responses=False)
    submitter = ProofSubmitter(
        web3, task_manager, redis_client, allow_development_backend=True
    )
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


@pytest.fixture(scope="module")
def chain() -> Iterator[tuple[Web3, Any, Any, Any]]:
    nodes = chain_helper.connect_or_start_node()
    web3 = next(nodes)
    deployment = chain_helper.install_team_a(web3)
    try:
        yield (
            web3,
            deployment["task_manager"],
            deployment["escrow"],
            deployment["token"],
        )
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
        task_manager.functions.submitProofs(
            [task_id], [proof_hash], [model_cid], [result_hash]
        ).transact()
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
            task_manager.functions.submitProofs(
                [task_id], [proof_hash], [model_cid], [result_hash]
            ).transact({"from": other})
        )


OPEN, ASSIGNED, COMPLETED, SETTLED = 0, 1, 2, 3


@pytest.fixture(scope="module")
def lifecycle_chain() -> Iterator[dict[str, Any]]:
    nodes = chain_helper.connect_or_start_node()
    web3 = next(nodes)
    try:
        yield chain_helper.install_team_a(web3) | {"web3": web3}
    finally:
        nodes.close()


def _open_funded_task(
    lifecycle_chain: dict[str, Any],
) -> tuple[Web3, Any, bytes, str, str, int]:
    web3 = lifecycle_chain["web3"]
    creator, worker = web3.eth.accounts[0], web3.eth.accounts[3]
    bounty = int(web3.to_wei(1, "ether"))
    task_id, _created, _funded = chain_helper.create_and_fund(
        web3,
        lifecycle_chain["task_manager"],
        lifecycle_chain["escrow"],
        lifecycle_chain["token"],
        bounty=bounty,
        creator=creator,
    )
    return web3, lifecycle_chain["task_manager"], task_id, creator, worker, bounty


def _assert_reverts_with(exc: BaseException, contract: str, error: str) -> None:
    assert chain_helper.revert_selector(exc) == chain_helper.error_selector(
        contract, error
    ), str(exc)


def test_accepted_bid_then_proof_then_settlement_pays_the_worker(
    lifecycle_chain: dict[str, Any],
) -> None:
    web3, task_manager, task_id, creator, worker, bounty = _open_funded_task(
        lifecycle_chain
    )
    escrow, settlement, token = (
        lifecycle_chain["escrow"],
        lifecycle_chain["settlement"],
        lifecycle_chain["token"],
    )

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
        task_manager.functions.submitProofs(
            [task_id], [proof_hash], ["QmModel"], [result_hash]
        ).transact({"from": creator})
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


def test_settlement_without_an_accepted_worker_is_refused(
    lifecycle_chain: dict[str, Any],
) -> None:
    """A proof alone does not make the bounty payable: someone must accept a bid.

    ``submitProofs`` is not gated on the bid flow, so this is exactly the state our
    submitter can produce on its own. Settlement then reverts in Team A's contract.
    """
    web3, task_manager, task_id, creator, _worker, _bounty = _open_funded_task(
        lifecycle_chain
    )
    escrow, settlement = lifecycle_chain["escrow"], lifecycle_chain["settlement"]

    web3.eth.wait_for_transaction_receipt(
        task_manager.functions.submitProofs(
            [task_id],
            [web3.keccak(text="proof")],
            ["QmModel"],
            [web3.keccak(text="result")],
        ).transact({"from": creator})
    )
    assert task_manager.functions.getStatus(task_id).call() == COMPLETED

    with pytest.raises(ContractLogicError) as error:
        chain_helper.settle(web3, settlement, task_id, creator)

    _assert_reverts_with(error.value, "SettlementContract", "NoWorkerAssigned")
    assert escrow.functions.getBalance(task_id).call() == web3.to_wei(1, "ether")


def test_only_the_creator_can_mark_a_task_complete(
    lifecycle_chain: dict[str, Any],
) -> None:
    web3, task_manager, task_id, creator, worker, bounty = _open_funded_task(
        lifecycle_chain
    )
    chain_helper.place_bid(web3, task_manager, task_id, worker, price=bounty)
    chain_helper.accept_bid(web3, task_manager, task_id, worker, creator)

    with pytest.raises(ContractLogicError) as error:
        chain_helper.mark_complete(web3, task_manager, task_id, worker)
    _assert_reverts_with(error.value, "TaskManager", "NotTaskCreator")

    chain_helper.mark_complete(web3, task_manager, task_id, creator)
    assert task_manager.functions.getStatus(task_id).call() == COMPLETED


def test_submit_proofs_is_gated_by_the_prover_allowlist(
    lifecycle_chain: dict[str, Any],
) -> None:
    web3, task_manager, task_id, _creator, _worker, _bounty = _open_funded_task(
        lifecycle_chain
    )
    outsider = list(web3.eth.accounts)[4]
    assert task_manager.functions.isProver(outsider).call() is False

    with pytest.raises(ContractLogicError) as error:
        task_manager.functions.submitProofs(
            [task_id],
            [web3.keccak(text="proof")],
            ["QmModel"],
            [web3.keccak(text="result")],
        ).transact({"from": outsider})

    _assert_reverts_with(error.value, "TaskManager", "NotProver")
    assert task_manager.functions.getStatus(task_id).call() == OPEN
