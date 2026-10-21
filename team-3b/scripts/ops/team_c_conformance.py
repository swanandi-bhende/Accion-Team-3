"""Validate Team C's agent traffic against the contract we documented (Step 18.6).

Team C has not sent a request yet, so there is nothing real to replay. This tool is
the check to run on the first payloads they send, and it also audits our own
assumption: a payload is validated against ``docs/INTEGRATION_CONTRACTS.md`` and,
optionally, replayed against a live ``POST /inference`` with the response checked.

Usage::

    python -m scripts.ops.team_c_conformance sample.json
    python -m scripts.ops.team_c_conformance sample.json --endpoint http://localhost:8000

Exit codes: 0 no errors (warnings allowed), 1 a contract error, 2 the endpoint could
not be reached.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

ERROR, WARNING = "error", "warning"
HEX_32 = re.compile(r"^[0-9a-f]{64}$")
DOCUMENTED_FIELDS = ("model_id", "input_data", "task_id")
RECEIPT_FIELDS = (
    "execution_id",
    "model_id",
    "model_cid",
    "input_hash",
    "output_hash",
    "output",
    "timestamp",
)


@dataclass(frozen=True)
class Finding:
    severity: str
    field: str
    detail: str

    def render(self) -> str:
        return f"FINDING={self.severity} FIELD={self.field} {self.detail}"


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def validate_payload(payload: Mapping[str, Any]) -> list[Finding]:
    """Check one ``InferenceRequest`` against the documented schema."""
    findings: list[Finding] = []

    for key in payload:
        if key not in DOCUMENTED_FIELDS:
            findings.append(
                Finding(WARNING, key, "not in the documented schema; pydantic ignores it silently")
            )

    model_id = payload.get("model_id")
    if not isinstance(model_id, str) or not model_id.strip():
        findings.append(Finding(ERROR, "model_id", "required non-empty string"))
    elif model_id != model_id.strip():
        findings.append(Finding(WARNING, "model_id", "leading/trailing whitespace will not match a registry key"))

    input_data = payload.get("input_data")
    if not isinstance(input_data, str):
        findings.append(Finding(ERROR, "input_data", "required string"))
    else:
        try:
            decoded = base64.b64decode(input_data, validate=True)
        except (binascii.Error, ValueError):
            findings.append(
                Finding(ERROR, "input_data", "does not decode as base64; the documented encoding is base64")
            )
        else:
            if decoded != input_data.encode():
                findings.append(
                    Finding(
                        WARNING,
                        "input_data",
                        "decodes to "
                        f"{len(decoded)} bytes, but DRIFT-003 means the service hashes and executes this "
                        "literal text instead of the decoded bytes",
                    )
                )
            elif not decoded:
                findings.append(Finding(WARNING, "input_data", "decodes to empty bytes"))

    if "task_id" not in payload or payload.get("task_id") in (None, ""):
        findings.append(
            Finding(WARNING, "task_id", "absent; the response cannot be correlated to a TaskCreated event")
        )
    elif not isinstance(payload["task_id"], str):
        findings.append(Finding(ERROR, "task_id", "must be a string when present"))
    else:
        task_id = payload["task_id"]
        if task_id.startswith("0x") and len(task_id) != 66:
            findings.append(
                Finding(WARNING, "task_id", "looks like an on-chain id but is not 32 bytes (0x + 64 hex)")
            )
    return findings


def validate_receipt(
    receipt: Mapping[str, Any], *, expected_model_id: str | None = None
) -> list[Finding]:
    """Check one ``ExecutionReceipt`` response against the documented schema."""
    findings: list[Finding] = []

    for key in RECEIPT_FIELDS:
        if key not in receipt:
            findings.append(Finding(ERROR, key, "missing from the response"))
    for key in receipt:
        if key not in RECEIPT_FIELDS:
            findings.append(Finding(WARNING, key, "unexpected field in the response"))

    if expected_model_id is not None and receipt.get("model_id") != expected_model_id:
        findings.append(
            Finding(
                ERROR,
                "model_id",
                f"response echoes {receipt.get('model_id')!r}, request asked for {expected_model_id!r}",
            )
        )

    for key in ("execution_id", "model_cid"):
        value = receipt.get(key)
        if key in receipt and (not isinstance(value, str) or not value):
            findings.append(Finding(ERROR, key, "must be a non-empty string"))

    for key in ("input_hash", "output_hash"):
        value = receipt.get(key)
        if key not in receipt:
            continue
        if not isinstance(value, str) or not HEX_32.match(value):
            findings.append(Finding(ERROR, key, "must be 64 lowercase hex characters"))

    timestamp = receipt.get("timestamp")
    if "timestamp" in receipt and not isinstance(timestamp, (int, float)):
        findings.append(Finding(ERROR, "timestamp", "must be a number"))

    output = receipt.get("output")
    if "output" in receipt and not isinstance(output, (str, bytes)):
        findings.append(Finding(ERROR, "output", "expected a string (bytes are serialized in JSON)"))
    if isinstance(output, str) and not output:
        findings.append(Finding(WARNING, "output", "empty execution output"))

    if "task_id" not in receipt:
        findings.append(
            Finding(
                WARNING,
                "task_id",
                "the response does not echo the request's task_id; callers must correlate by request order",
            )
        )
    return findings


def post_payload(
    endpoint: str, payload: Mapping[str, Any], *, timeout_s: float = 120.0
) -> tuple[int, dict[str, Any]]:
    """POST one payload to the inference service; returns ``(status, body)``."""
    url = endpoint.rstrip("/") + "/inference"
    response = httpx.post(url, json=payload, timeout=timeout_s)
    body: Any
    try:
        body = response.json()
    except json.JSONDecodeError:
        body = {"_raw": response.text[:2000]}
    if not isinstance(body, dict):
        body = {"_body": body}
    return response.status_code, body


def load_payload(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def errors(findings: list[Finding]) -> bool:
    return any(finding.severity == ERROR for finding in findings)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("payload", type=Path, help="a JSON InferenceRequest captured from Team C")
    parser.add_argument("--endpoint", default="", help="base URL of a running inference service")
    parser.add_argument("--timeout", type=float, default=120.0)
    args = parser.parse_args(argv)

    try:
        payload = load_payload(args.payload)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"FINDING=error FIELD=payload {error}")
        return 1

    findings = validate_payload(payload)

    if args.endpoint:
        try:
            status, body = post_payload(args.endpoint, payload, timeout_s=args.timeout)
        except httpx.HTTPError as error:
            print(f"FINDING=error FIELD=endpoint request failed: {error}")
            return 2
        print(f"HTTP status={status}")
        if status != 200:
            print(f"RESPONSE {json.dumps(body)[:1000]}")
        else:
            findings.extend(validate_receipt(body, expected_model_id=payload.get("model_id")))
    else:
        print("FINDING=warning FIELD=endpoint not given; payload validated but not replayed")

    for finding in findings:
        print(finding.render())
    return 1 if errors(findings) else 0


if __name__ == "__main__":
    raise SystemExit(main())
