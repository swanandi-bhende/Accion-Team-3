"""Team C contract checks: the payload we documented, and the response we send."""

import base64
from typing import Any

from scripts.ops.team_c_conformance import validate_payload, validate_receipt

HEX = "ab" * 32
RECEIPT: dict[str, Any] = {
    "execution_id": "1f2e3d4c",
    "model_id": "toy_linear",
    "model_cid": "QmModel",
    "input_hash": HEX,
    "output_hash": HEX,
    "output": "aGVsbG8=",
    "timestamp": 1.0,
}


def errors(findings: Any) -> list[str]:
    return [finding.field for finding in findings if finding.severity == "error"]


def warnings(findings: Any) -> list[str]:
    return [finding.field for finding in findings if finding.severity == "warning"]


def test_documented_payload_has_no_errors() -> None:
    findings = validate_payload(
        {
            "model_id": "toy_linear",
            "input_data": base64.b64encode(b"hello").decode(),
            "task_id": "0x" + "01" * 32,
        }
    )

    assert errors(findings) == []


def test_missing_model_id_is_an_error() -> None:
    findings = validate_payload({"input_data": "aGVsbG8="})

    assert errors(findings) == ["model_id"]


def test_non_base64_input_is_an_error_because_the_document_requires_base64() -> None:
    findings = validate_payload({"model_id": "toy_linear", "input_data": "not base64!!"})

    assert errors(findings) == ["input_data"]


def test_plain_text_that_happens_to_be_base64_raises_the_encoding_drift() -> None:
    # "aGVs" is itself valid base64 (it decodes to three other bytes), which is
    # exactly how DRIFT-003 slips through unnoticed.
    findings = validate_payload({"model_id": "toy_linear", "input_data": "aGVs", "task_id": "7"})

    assert errors(findings) == []
    assert "input_data" in warnings(findings)


def test_absent_task_id_warns_that_the_response_cannot_be_correlated() -> None:
    findings = validate_payload({"model_id": "toy_linear", "input_data": "aGVsbG8="})

    assert "task_id" in warnings(findings)


def test_task_id_that_is_not_bytes32_hex_warns() -> None:
    findings = validate_payload(
        {"model_id": "toy_linear", "input_data": "aGVsbG8=", "task_id": "0x1234"}
    )

    assert "task_id" in warnings(findings)


def test_undocumented_field_warns_rather_than_failing() -> None:
    findings = validate_payload(
        {"model_id": "toy_linear", "input_data": "aGVsbG8=", "task_id": "7", "priority": 1}
    )

    assert errors(findings) == []
    assert "priority" in warnings(findings)


def test_documented_receipt_has_no_errors() -> None:
    assert errors(validate_receipt(dict(RECEIPT), expected_model_id="toy_linear")) == []


def test_receipt_missing_output_hash_is_an_error() -> None:
    receipt = dict(RECEIPT)
    del receipt["output_hash"]

    assert "output_hash" in errors(validate_receipt(receipt))


def test_receipt_must_echo_the_requested_model() -> None:
    assert errors(validate_receipt(dict(RECEIPT), expected_model_id="toy_mlp")) == ["model_id"]


def test_receipt_hashes_must_be_lowercase_hex_32_bytes() -> None:
    receipt = dict(RECEIPT, input_hash="NOT-A-HASH")

    assert "input_hash" in errors(validate_receipt(receipt))


def test_receipt_does_not_echo_task_id() -> None:
    # ExecutionReceipt has no task_id field, so Team C has to correlate by order.
    assert "task_id" in warnings(validate_receipt(dict(RECEIPT)))
