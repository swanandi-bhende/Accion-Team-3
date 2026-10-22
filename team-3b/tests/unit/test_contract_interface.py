"""Contract-interface checks against Team A's delivered ABI.

Team A's exports in ``team_a_assets/`` are the source of truth. The docs
(``docs/INTEGRATION_CONTRACTS.md``) quote the same shapes, and the parity tests at
the bottom fail if our local mock ever drifts from the real ABI again -- which is
what DRIFT-001 and DRIFT-002 were.
"""

from pathlib import Path
from typing import Any

import pytest

from event_listener.config import artifact_candidates, load_contract_abi

ROOT = Path(__file__).resolve().parents[2]
TEAM_A_ARTIFACTS = ROOT / "team_a_assets"


def event_inputs(abi: list[dict[str, Any]], name: str) -> dict[str, dict[str, Any]]:
    event = next(item for item in abi if item.get("type") == "event" and item.get("name") == name)
    return {item["name"]: item for item in event["inputs"]}


def function_inputs(abi: list[dict[str, Any]], name: str) -> dict[str, dict[str, Any]]:
    function = next(item for item in abi if item.get("type") == "function" and item.get("name") == name)
    return {item["name"]: item for item in function["inputs"]}


def function_outputs(abi: list[dict[str, Any]], name: str) -> list[str]:
    function = next(item for item in abi if item.get("type") == "function" and item.get("name") == name)
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


# --- Team A's delivered interfaces -------------------------------------------


def test_task_created_matches_documented_fields(task_abi: list[dict[str, Any]]) -> None:
    inputs = event_inputs(task_abi, "TaskCreated")

    assert inputs["taskId"]["type"] == "bytes32"
    assert inputs["taskId"]["indexed"] is True
    assert inputs["creator"]["indexed"] is True
    assert inputs["creator"]["type"] == "address"
    assert inputs["descriptionHash"]["type"] == "bytes32"
    assert inputs["deadline"]["type"] == "uint256"
    assert inputs["bountyAmount"]["type"] == "uint256"


def test_task_funded_matches_documented_fields(escrow_abi: list[dict[str, Any]]) -> None:
    inputs = event_inputs(escrow_abi, "TaskFunded")

    assert inputs["taskId"]["type"] == "bytes32"
    assert inputs["taskId"]["indexed"] is True
    assert inputs["funder"]["indexed"] is True
    assert inputs["funder"]["type"] == "address"
    assert inputs["amount"]["type"] == "uint256"


def test_submit_proofs_takes_four_parallel_arrays(task_abi: list[dict[str, Any]]) -> None:
    inputs = function_inputs(task_abi, "submitProofs")

    assert [inputs[name]["type"] for name in ("taskIds", "proofHashes", "modelCids", "resultHashes")] == [
        "bytes32[]",
        "bytes32[]",
        "string[]",
        "bytes32[]",
    ]


def test_submit_proofs_reports_how_many_were_accepted(task_abi: list[dict[str, Any]]) -> None:
    # DRIFT-004: the contract can accept a subset of a batch, so our retry logic
    # must not assume an all-or-nothing revert.
    assert function_outputs(task_abi, "submitProofs") == ["uint256"]


def test_submit_proofs_is_bounded_by_max_proof_batch(task_abi: list[dict[str, Any]]) -> None:
    assert function_outputs(task_abi, "MAX_PROOF_BATCH") == ["uint256"]


def test_proof_submitter_requires_an_allowlisted_signer(task_abi: list[dict[str, Any]]) -> None:
    assert function_outputs(task_abi, "isProver") == ["bool"]
    assert [item["type"] for item in function_inputs(task_abi, "setProver").values()] == [
        "address",
        "bool",
    ]


def test_proof_submission_emits_accepted_and_rejected_events(task_abi: list[dict[str, Any]]) -> None:
    assert event_inputs(task_abi, "ProofSubmitted")["submitter"]["indexed"] is True
    assert event_inputs(task_abi, "ProofRejected")["reason"]["type"] == "uint8"


def test_settlement_contract_settles_a_completed_task(settlement_abi: list[dict[str, Any]]) -> None:
    # Settlement is a separate contract; the submitter only records the proof.
    assert [item["type"] for item in function_inputs(settlement_abi, "settleTask").values()] == [
        "bytes32"
    ]
    assert event_inputs(settlement_abi, "TaskSettled")["workerAmount"]["type"] == "uint256"


# --- Mock parity: retired with the stubs -------------------------------------
#
# The local stack now runs Team A's deployed bytecode (scripts/ops/
# install_team_a_local.py), so the old "our stub matches their ABI" parity tests
# are gone with the stubs in mocks/legacy_mock_contracts/. What remains are
# assertions against Team A's own exports.


# --- Artifact resolution ------------------------------------------------------


def test_bare_abi_export_is_discovered(tmp_path: Path) -> None:
    (tmp_path / "Widget.abi.json").write_text('[{"type": "event", "name": "Ping", "inputs": []}]')

    assert load_contract_abi("Widget", tmp_path) == [{"type": "event", "name": "Ping", "inputs": []}]


def test_hardhat_artifact_wins_over_bare_abi(tmp_path: Path) -> None:
    (tmp_path / "Widget.sol").mkdir()
    (tmp_path / "Widget.sol" / "Widget.json").write_text('{"abi": [{"type": "error", "name": "Boom"}]}')
    (tmp_path / "Widget.abi.json").write_text('[{"type": "error", "name": "Other"}]')

    assert load_contract_abi("Widget", tmp_path) == [{"type": "error", "name": "Boom"}]


def test_missing_artifact_names_every_searched_path(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError) as error:
        load_contract_abi("Missing", tmp_path)

    message = str(error.value)
    assert all(str(path) in message for path in artifact_candidates("Missing", tmp_path))
