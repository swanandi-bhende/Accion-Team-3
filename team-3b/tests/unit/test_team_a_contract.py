"""Team A's contract: the ABI we load, the chain and signer guards, and how the pipeline is wired to it."""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import fakeredis.aioredis as fakeredis
import pytest
from eth_abi import encode as abi_encode
from web3 import Web3

from common.config import Settings
from common.constants import DEVELOPMENT_PROOF_BACKEND
from common.exceptions import ChainMismatchError, ConfigurationError
from common.models import AttestationReport, ProofArtifact
from common.web3_checks import (
    prover_allowlist_status,
    signer_address,
    verify_chain_id,
)
from event_listener.config import artifact_candidates, load_contract_abi
from event_listener.listener import EventListener
from inference.pipeline import build_pipeline
from proof_submitter.submitter import PendingProof, ProofSubmitter

ROOT = Path(__file__).resolve().parents[2]

TEAM_A_ARTIFACTS = ROOT / "team_a_assets"


def event_inputs(abi: list[dict[str, Any]], name: str) -> dict[str, dict[str, Any]]:
    event = next(
        item for item in abi if item.get("type") == "event" and item.get("name") == name
    )
    return {item["name"]: item for item in event["inputs"]}


def function_inputs(abi: list[dict[str, Any]], name: str) -> dict[str, dict[str, Any]]:
    function = next(
        item
        for item in abi
        if item.get("type") == "function" and item.get("name") == name
    )
    return {item["name"]: item for item in function["inputs"]}


def function_outputs(abi: list[dict[str, Any]], name: str) -> list[str]:
    function = next(
        item
        for item in abi
        if item.get("type") == "function" and item.get("name") == name
    )
    return [item["type"] for item in function["outputs"]]


def event_shape(abi: list[dict[str, Any]], name: str) -> list[tuple[str, str, bool]]:
    return [
        (item["name"], item["type"], bool(item.get("indexed")))
        for item in event_inputs(abi, name).values()
    ]


def call_shape(abi: list[dict[str, Any]], name: str) -> tuple[list[str], list[str]]:
    return (
        [item["type"] for item in function_inputs(abi, name).values()],
        function_outputs(abi, name),
    )


@pytest.fixture(scope="module")
def task_abi() -> list[dict[str, Any]]:
    return load_contract_abi("TaskManager", TEAM_A_ARTIFACTS)


@pytest.fixture(scope="module")
def escrow_abi() -> list[dict[str, Any]]:
    return load_contract_abi("EscrowContract", TEAM_A_ARTIFACTS)


@pytest.fixture(scope="module")
def settlement_abi() -> list[dict[str, Any]]:
    return load_contract_abi("SettlementContract", TEAM_A_ARTIFACTS)


def test_task_created_matches_documented_fields(task_abi: list[dict[str, Any]]) -> None:
    inputs = event_inputs(task_abi, "TaskCreated")

    assert inputs["taskId"]["type"] == "bytes32"
    assert inputs["taskId"]["indexed"] is True
    assert inputs["creator"]["indexed"] is True
    assert inputs["creator"]["type"] == "address"
    assert inputs["descriptionHash"]["type"] == "bytes32"
    assert inputs["deadline"]["type"] == "uint256"
    assert inputs["bountyAmount"]["type"] == "uint256"


def test_task_funded_matches_documented_fields(
    escrow_abi: list[dict[str, Any]],
) -> None:
    inputs = event_inputs(escrow_abi, "TaskFunded")

    assert inputs["taskId"]["type"] == "bytes32"
    assert inputs["taskId"]["indexed"] is True
    assert inputs["funder"]["indexed"] is True
    assert inputs["funder"]["type"] == "address"
    assert inputs["amount"]["type"] == "uint256"


def test_submit_proofs_takes_four_parallel_arrays(
    task_abi: list[dict[str, Any]],
) -> None:
    inputs = function_inputs(task_abi, "submitProofs")

    assert [
        inputs[name]["type"]
        for name in ("taskIds", "proofHashes", "modelCids", "resultHashes")
    ] == [
        "bytes32[]",
        "bytes32[]",
        "string[]",
        "bytes32[]",
    ]


def test_submit_proofs_reports_how_many_were_accepted(
    task_abi: list[dict[str, Any]],
) -> None:
    # DRIFT-004: the contract can accept a subset of a batch, so our retry logic
    # must not assume an all-or-nothing revert.
    assert function_outputs(task_abi, "submitProofs") == ["uint256"]


def test_submit_proofs_is_bounded_by_max_proof_batch(
    task_abi: list[dict[str, Any]],
) -> None:
    assert function_outputs(task_abi, "MAX_PROOF_BATCH") == ["uint256"]


def test_proof_submitter_requires_an_allowlisted_signer(
    task_abi: list[dict[str, Any]],
) -> None:
    assert function_outputs(task_abi, "isProver") == ["bool"]
    assert [
        item["type"] for item in function_inputs(task_abi, "setProver").values()
    ] == [
        "address",
        "bool",
    ]


def test_proof_submission_emits_accepted_and_rejected_events(
    task_abi: list[dict[str, Any]],
) -> None:
    assert event_inputs(task_abi, "ProofSubmitted")["submitter"]["indexed"] is True
    assert event_inputs(task_abi, "ProofRejected")["reason"]["type"] == "uint8"


def test_settlement_contract_settles_a_completed_task(
    settlement_abi: list[dict[str, Any]],
) -> None:
    # Settlement is a separate contract; the submitter only records the proof.
    assert [
        item["type"] for item in function_inputs(settlement_abi, "settleTask").values()
    ] == ["bytes32"]
    assert (
        event_inputs(settlement_abi, "TaskSettled")["workerAmount"]["type"] == "uint256"
    )


def test_bare_abi_export_is_discovered(tmp_path: Path) -> None:
    (tmp_path / "Widget.abi.json").write_text(
        '[{"type": "event", "name": "Ping", "inputs": []}]'
    )

    assert load_contract_abi("Widget", tmp_path) == [
        {"type": "event", "name": "Ping", "inputs": []}
    ]


def test_hardhat_artifact_wins_over_bare_abi(tmp_path: Path) -> None:
    (tmp_path / "Widget.sol").mkdir()
    (tmp_path / "Widget.sol" / "Widget.json").write_text(
        '{"abi": [{"type": "error", "name": "Boom"}]}'
    )
    (tmp_path / "Widget.abi.json").write_text('[{"type": "error", "name": "Other"}]')

    assert load_contract_abi("Widget", tmp_path) == [{"type": "error", "name": "Boom"}]


def test_missing_artifact_names_every_searched_path(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError) as error:
        load_contract_abi("Missing", tmp_path)

    message = str(error.value)
    assert all(
        str(path) in message for path in artifact_candidates("Missing", tmp_path)
    )


TEAM_A_TASK_MANAGER = "0xA55f34e4137aD55f48aB268d4361F2225C4B5078"

TEST_PRIVATE_KEY = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"

TEST_ADDRESS = "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266"


def web3_with(
    chain_id: Any = 16602, accounts: list[str] | None = None
) -> SimpleNamespace:
    return SimpleNamespace(
        eth=SimpleNamespace(chain_id=chain_id, accounts=accounts or []),
        provider=SimpleNamespace(endpoint_uri="http://rpc.example"),
    )


def test_matching_chain_id_passes() -> None:
    assert verify_chain_id(web3_with(16602), 16602, "event-listener") == 16602


def test_wrong_chain_id_names_both_chains() -> None:
    with pytest.raises(ChainMismatchError) as error:
        verify_chain_id(web3_with(31337), 16602, "proof-submitter")

    message = str(error.value)
    assert "31337" in message and "16602" in message
    assert "WEB3_CHAIN_ID" in message


def test_unset_expected_chain_id_accepts_any_chain() -> None:
    assert verify_chain_id(web3_with(31337), None, "event-listener") == 31337


class UnreachableEth:
    @property
    def chain_id(self) -> int:
        raise ConnectionError("rpc down")


def test_unreadable_chain_id_is_not_fatal() -> None:
    web3 = SimpleNamespace(eth=UnreachableEth(), provider=None)

    assert verify_chain_id(web3, 16602, "event-listener") is None


def test_private_key_wins_over_node_accounts() -> None:
    assert (
        signer_address(web3_with(accounts=["0x" + "11" * 20]), TEST_PRIVATE_KEY, "s")
        == TEST_ADDRESS
    )


def test_invalid_private_key_is_a_configuration_error() -> None:
    with pytest.raises(ConfigurationError):
        signer_address(web3_with(accounts=["0x" + "11" * 20]), "0xnot-a-key", "s")


def test_falls_back_to_the_node_account() -> None:
    assert (
        signer_address(web3_with(accounts=["0x" + "22" * 20]), "", "s")
        == "0x" + "22" * 20
    )


def test_no_signer_available_returns_none() -> None:
    assert signer_address(web3_with(), "", "s") is None


def test_zero_placeholder_key_falls_back_to_the_node_account() -> None:
    web3 = web3_with(accounts=["0x" + "22" * 20])

    assert signer_address(web3, "0x" + "0" * 64, "s") == "0x" + "22" * 20


class ProverFunctions:
    def __init__(self, allowed: bool) -> None:
        self.allowed = allowed
        self.queried: list[str] = []

    def isProver(self, address: str) -> "ProverFunctions":
        self.queried.append(address)
        return self

    def call(self) -> bool:
        return self.allowed


def test_allowlisted_signer_is_reported_as_allowed() -> None:
    functions = ProverFunctions(allowed=True)

    assert (
        prover_allowlist_status(SimpleNamespace(functions=functions), TEST_ADDRESS)
        is True
    )
    assert functions.queried == [TEST_ADDRESS]


def test_absent_is_prover_view_means_unknown() -> None:
    assert (
        prover_allowlist_status(
            SimpleNamespace(functions=SimpleNamespace()), TEST_ADDRESS
        )
        is None
    )


class FakeFunctions:
    """Minimal stand-in for ``contract.functions`` with the Team A ABI."""

    def __init__(
        self, max_batch: int | None, reject_task_ids: set[bytes] | None = None
    ) -> None:
        self.max_batch = max_batch
        self.reject_task_ids = reject_task_ids or set()
        self.calls: list[list[bytes]] = []

    @property
    def MAX_PROOF_BATCH(self) -> Any:
        if self.max_batch is None:
            raise AttributeError("older ABI has no MAX_PROOF_BATCH")
        return lambda: SimpleNamespace(call=lambda: self.max_batch)

    def submitProofs(
        self,
        task_ids: list[bytes],
        proof_hashes: list[bytes],
        model_cids: list[str],
        result_hashes: list[bytes],
    ) -> "FakeTransaction":
        return FakeTransaction(self, task_ids)


class FakeTransaction:
    def __init__(self, functions: FakeFunctions, task_ids: list[bytes]) -> None:
        self.functions = functions
        self.task_ids = task_ids

    def build_transaction(self, options: dict[str, Any]) -> dict[str, Any]:
        return options

    def transact(self, options: dict[str, Any]) -> str:
        # One recorded entry per on-chain transaction (submitProofs itself is
        # also called to build the transaction).
        self.functions.calls.append(self.task_ids)
        return "0xtx"


class FakeEvent:
    def __init__(self, task_ids: list[bytes]) -> None:
        self.task_ids = task_ids

    def process_receipt(self, _receipt: Any) -> list[dict[str, Any]]:
        return [{"args": {"taskId": task_id, "reason": 1}} for task_id in self.task_ids]


class FakeContract:
    def __init__(
        self, functions: FakeFunctions, rejected: list[bytes] | None = None
    ) -> None:
        self.functions = functions
        self.events = SimpleNamespace(ProofRejected=lambda: FakeEvent(rejected or []))


class FakeEth:
    accounts = ["0x" + "33" * 20]

    def get_transaction_count(self, _account: str) -> int:
        return 0

    def wait_for_transaction_receipt(self, _tx: str) -> dict[str, int]:
        return {"status": 1}


def submitter(contract: FakeContract, redis_client: Any = None) -> ProofSubmitter:
    return ProofSubmitter(
        web3=SimpleNamespace(eth=FakeEth()),
        contract=contract,
        redis_client=redis_client or fakeredis.FakeRedis(decode_responses=False),
        sleep=lambda _delay: asyncio.sleep(0),
        batch_window=0.0,
        allow_development_backend=True,
    )


def payload(task_id: str) -> tuple[str, AttestationReport, ProofArtifact, str]:
    attestation = AttestationReport(
        enclave_measurement="m",
        signer_identity="s",
        input_hash="i",
        output_hash="o",
        hardware_signature=b"sig",
        timestamp=1.0,
    )
    proof = ProofArtifact(
        proof_bytes=b"proof",
        public_inputs={"output_hash": "o"},
        verification_key_ref="vk",
        backend=DEVELOPMENT_PROOF_BACKEND,
    )
    return task_id, attestation, proof, "QmModel"


def pending(task_id: str) -> PendingProof:
    """Build a buffered proof directly, so the batch timer never fires twice."""
    _, attestation, proof, model_cid = payload(task_id)
    return PendingProof(
        task_id, attestation, proof, model_cid, "proof-hash", "result-hash"
    )


@pytest.mark.asyncio
async def test_batches_are_split_to_max_proof_batch() -> None:
    functions = FakeFunctions(max_batch=2)
    instance = submitter(FakeContract(functions))
    proofs = [pending(f"task-{index}") for index in range(5)]

    await instance._submit_batch(proofs)

    assert [len(call) for call in functions.calls] == [2, 2, 1]


@pytest.mark.asyncio
async def test_contract_without_max_proof_batch_sends_one_transaction() -> None:
    functions = FakeFunctions(max_batch=None)
    instance = submitter(FakeContract(functions))
    proofs = [pending(f"task-{index}") for index in range(3)]

    await instance._submit_batch(proofs)

    assert [len(call) for call in functions.calls] == [3]


@pytest.mark.asyncio
async def test_rejected_proofs_are_dead_lettered_without_retrying_the_batch() -> None:
    redis_client = fakeredis.FakeRedis(decode_responses=False)
    rejected_id = ProofSubmitter._bytes32("task-1")
    functions = FakeFunctions(max_batch=None)
    instance = submitter(FakeContract(functions, rejected=[rejected_id]), redis_client)
    proofs = [pending("task-0"), pending("task-1")]

    tx_hash = await instance._submit_batch(proofs)

    assert tx_hash == "0xtx"
    assert len(functions.calls) == 1  # no retry for a partial success
    assert instance.metrics["proofs_submitted"] == 1
    assert instance.metrics["rejected_by_contract"] == 1
    client: Any = redis_client
    entry = json.loads((await client.lindex(ProofSubmitter.DLQ_KEY, 0)).decode())
    assert [proof["task_id"] for proof in entry["proofs"]] == ["task-1"]
    assert "ProofRejected" in entry["last_error"]


def team_a_contract() -> Any:
    return Web3().eth.contract(
        address=Web3.to_checksum_address(TEAM_A_TASK_MANAGER),
        abi=load_contract_abi("TaskManager", TEAM_A_ARTIFACTS),
    )


def test_real_abi_accepts_our_submit_proofs_payload() -> None:
    contract = team_a_contract()
    calldata = contract.encode_abi(
        "submitProofs",
        args=[[b"\x01" * 32], [b"\x02" * 32], ["QmModel"], [b"\x03" * 32]],
    )

    selector = Web3.keccak(text="submitProofs(bytes32[],bytes32[],string[],bytes32[])")[
        :4
    ].hex()
    assert calldata.startswith("0x" + selector)


def test_real_abi_proof_rejected_log_is_mapped_to_its_proof() -> None:
    contract = team_a_contract()
    task_id = ProofSubmitter._bytes32("task-1")
    log = {
        "address": contract.address,
        "topics": [Web3.keccak(text="ProofRejected(bytes32,uint8)"), task_id],
        "data": "0x" + abi_encode(["uint8"], [3]).hex(),
        "logIndex": 0,
        "transactionIndex": 0,
        "transactionHash": b"\x04" * 32,
        "blockHash": b"\x05" * 32,
        "blockNumber": 10,
        "removed": False,
    }
    receipt = {
        "status": 1,
        "logs": [log],
        "transactionHash": b"\x04" * 32,
        "blockNumber": 10,
        "gasUsed": 21000,
    }
    instance = submitter(FakeContract(FakeFunctions(max_batch=None)))
    instance.contract = contract

    rejected = instance._rejected_from_receipt(
        receipt, [pending("task-0"), pending("task-1")]
    )

    assert [proof.task_id for proof in rejected] == ["task-1"]


@pytest.mark.asyncio
async def test_listener_reads_team_a_amount_field() -> None:
    listener = EventListener(
        web3=SimpleNamespace(),
        task_manager=SimpleNamespace(),
        escrow=SimpleNamespace(),
        redis_client=fakeredis.FakeRedis(decode_responses=False),
    )

    await listener._process_funded(
        {"args": {"taskId": "0x01", "funder": "0xf", "amount": 7}, "blockNumber": 1}
    )

    assert listener._task_events["0x01"]["funded"]["funded_amount"] == 7


TASK_MANAGER_ADDRESS = "0xA55f34e4137aD55f48aB268d4361F2225C4B5078"

ESCROW_ADDRESS = "0x5B685Ca03A8072FB3f9721Dd7017Ed0D8C719217"


def test_build_pipeline_wires_team_a_contracts_from_their_artifact_names() -> None:
    """The composed pipeline must load Team A's ABIs by their shipped artifact names."""
    settings = Settings(
        redis_url="redis://127.0.0.1:6379",
        web3_rpc_url="http://127.0.0.1:8545",
        task_manager_address=TASK_MANAGER_ADDRESS,
        escrow_address=ESCROW_ADDRESS,
        tee_mode="sgx",
        # None overrides .env, so building the pipeline never writes a key file into the repo.
        tee_sealed_key_path=None,
    )

    pipeline = build_pipeline(settings)

    listener = pipeline.listener
    assert listener.task_manager.abi == load_contract_abi(
        "TaskManager", TEAM_A_ARTIFACTS
    )
    assert listener.escrow.abi == load_contract_abi("EscrowContract", TEAM_A_ARTIFACTS)
    assert listener.escrow.address == Web3.to_checksum_address(ESCROW_ADDRESS)
