import json
from pathlib import Path

import pytest

from common.config import PLACEHOLDER_ADDRESS, Settings, get_settings


def write_deployments(path: Path) -> Path:
    path.write_text(
        json.dumps(
            {
                "TASK_MANAGER_ADDRESS": "0x" + "a" * 40,
                "ESCROW_ADDRESS": "0x" + "b" * 40,
                "PROOF_SUBMITTER_CONTRACT_ADDRESS": "0x" + "a" * 40,
            }
        ),
        encoding="utf-8",
    )
    return path


def test_placeholder_address_is_replaced_by_mock_deployment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    deployments = write_deployments(tmp_path / "localhost.json")

    settings = Settings(
        task_manager_address=PLACEHOLDER_ADDRESS,
        mock_deployments_path=str(deployments),
    )

    assert settings.task_manager_address == "0x" + "a" * 40


def test_explicit_real_address_wins_over_mock_deployment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    deployments = write_deployments(tmp_path / "localhost.json")
    real = "0x" + "1" * 40

    settings = Settings(
        task_manager_address=real,
        mock_deployments_path=str(deployments),
    )

    assert settings.task_manager_address == real


def test_placeholder_private_key_is_treated_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROOF_SUBMITTER_PRIVATE_KEY", "0x" + "0" * 64)
    get_settings.cache_clear()
    try:
        assert Settings().proof_submitter_private_key == ""
    finally:
        get_settings.cache_clear()


def test_blank_chain_id_means_no_check(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WEB3_CHAIN_ID", "")
    get_settings.cache_clear()
    try:
        assert Settings().web3_chain_id is None
    finally:
        get_settings.cache_clear()


def test_contract_artifact_dir_follows_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from event_listener.config import contract_artifact_dir

    monkeypatch.setenv("CONTRACT_ARTIFACTS_DIR", str(tmp_path))
    get_settings.cache_clear()
    try:
        assert contract_artifact_dir() == tmp_path
    finally:
        get_settings.cache_clear()


def test_bare_abi_list_is_accepted(tmp_path: Path) -> None:
    from event_listener.config import load_contract_abi

    (tmp_path / "TaskManager.sol").mkdir()
    abi = [{"type": "event", "name": "TaskCreated", "inputs": []}]
    (tmp_path / "TaskManager.sol" / "TaskManager.json").write_text(json.dumps(abi), encoding="utf-8")

    assert load_contract_abi("TaskManager", artifact_dir=tmp_path) == abi
