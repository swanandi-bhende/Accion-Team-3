"""The cross-team driver's contract-side helpers, without touching a chain."""

from typing import Any

import pytest

from scripts.ops import cross_team_e2e, team_a
from scripts.ops.team_a_preflight import Check, failed, render
from tests.onnx_fixtures import tensor_b64


def test_reference_payload_fills_task_and_model() -> None:
    payload = cross_team_e2e.reference_payload("toy_linear", "0x" + "01" * 32)

    assert payload["model_id"] == "toy_linear"
    assert payload["task_id"] == "0x" + "01" * 32
    assert payload["input_data"]


def test_hex_to_bytes32_round_trips_a_digest() -> None:
    digest = "ab" * 32

    assert team_a.hex_to_bytes32(digest) == bytes.fromhex(digest)
    assert team_a.hex_to_bytes32("0x" + digest) == bytes.fromhex(digest)


def test_hex_to_bytes32_rejects_the_wrong_width() -> None:
    with pytest.raises(ValueError):
        team_a.hex_to_bytes32("0x1234")


def test_receipt_findings_report_a_wrong_model() -> None:
    receipt: dict[str, Any] = {
        "execution_id": "e",
        "model_id": "toy_mlp",
        "model_cid": "QmModel",
        "input_hash": "ab" * 32,
        "output_hash": "ab" * 32,
        "output": "aGVsbG8=",
        "timestamp": 1.0,
    }
    payload = {"model_id": "toy_linear", "input_data": tensor_b64(), "task_id": "7"}

    findings = cross_team_e2e.receipt_findings(receipt, payload)

    assert [finding.field for finding in findings if finding.severity == "error"] == ["model_id"]


def test_missing_inference_endpoint_exits_two(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cross_team_e2e, "Web3", lambda *args, **kwargs: pytest.fail("must not dial"))
    monkeypatch.delenv("INFERENCE_URL", raising=False)

    assert cross_team_e2e.main([]) == 2


def test_preflight_render_and_failure_helpers() -> None:
    checks = [
        Check("chain_id", "passed", "16602"),
        Check("prover", "failed", "not allow-listed"),
        Check("signer", "skipped", "none"),
    ]

    assert failed(checks) is True
    assert "CHECK=prover STATUS=failed" in render(checks)
    assert failed([Check("chain_id", "passed", "16602")]) is False
