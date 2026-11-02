"""InferenceRequest against the payload documented in docs/INTEGRATION.md."""

import base64

from common.models import InferenceRequest


def test_documented_payload_parses_with_task_id() -> None:
    request = InferenceRequest.model_validate(
        {"model_id": "toy_linear", "input_data": "aGVsbG8=", "task_id": "7"}
    )

    assert request.model_id == "toy_linear"
    assert request.task_id == "7"


def test_task_id_is_optional() -> None:
    request = InferenceRequest.model_validate({"model_id": "toy_linear", "input_data": "aGVsbG8="})

    assert request.task_id is None


def test_input_data_is_decoded_from_base64() -> None:
    request = InferenceRequest.model_validate({"model_id": "toy_linear", "input_data": "aGVsbG8="})

    assert request.input_data == base64.b64decode("aGVsbG8=")
