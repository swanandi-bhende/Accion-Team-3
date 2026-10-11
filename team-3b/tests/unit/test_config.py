import json
from pathlib import Path

import pytest

from common.config import PLACEHOLDER_ADDRESS, Settings


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
