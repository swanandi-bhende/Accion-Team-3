"""Concrete TEE and zkML verification implementations."""

import base64
import json
import time
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from common.constants import TEE_ATTESTATION_EXPIRY_S
from common.models import AttestationReport, ProofArtifact
from proof_generator.verifier import ProofVerifier


def serialize_attestation(report: AttestationReport, signer_public_key: bytes) -> bytes:
    report_payload = {
        "enclave_measurement": report.enclave_measurement,
        "signer_identity": report.signer_identity,
        "input_hash": report.input_hash,
        "output_hash": report.output_hash,
        "hardware_signature": base64.b64encode(report.hardware_signature).decode(),
        "timestamp": report.timestamp,
    }
    payload = {
        "report": report_payload,
        "signer_public_key": base64.b64encode(signer_public_key).decode(),
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()


class ConcreteTEEVerifier:
    def __init__(self, trusted_public_keys: dict[str, bytes], expiry_s: float = TEE_ATTESTATION_EXPIRY_S):
        self.trusted_public_keys = trusted_public_keys
        self.expiry_s = expiry_s

    async def verify(self, proof_bytes: bytes, public_inputs: dict[str, Any]) -> tuple[bool, str]:
        try:
            payload = json.loads(proof_bytes)
            report_payload = dict(payload["report"])
            report_payload["hardware_signature"] = base64.b64decode(
                report_payload["hardware_signature"], validate=True
            )
            report = AttestationReport.model_validate(report_payload)
            signer_public_key = base64.b64decode(payload["signer_public_key"], validate=True)
        except Exception:
            return False, "malformed_proof"

        if time.time() - report.timestamp > self.expiry_s:
            return False, "expired_attestation"
        trusted_key = self.trusted_public_keys.get(report.signer_identity)
        if trusted_key is None or trusted_key != signer_public_key:
            return False, "invalid_signature"

        signed_payload = f"{report.enclave_measurement}:{report.signer_identity}:{report.input_hash}:{report.output_hash}".encode()
        try:
            Ed25519PublicKey.from_public_bytes(trusted_key).verify(report.hardware_signature, signed_payload)
        except Exception:
            return False, "invalid_signature"
        if report.input_hash != public_inputs.get("input_hash") or report.output_hash != public_inputs.get("output_hash"):
            return False, "proof_mismatch"
        return True, "verified"


class ConcreteZkMLVerifier:
    def __init__(self):
        self._verifier = ProofVerifier()

    async def verify(self, proof_bytes: bytes, public_inputs: dict[str, Any]) -> tuple[bool, str]:
        try:
            artifact_payload = json.loads(proof_bytes)
            if "proof_bytes" in artifact_payload and isinstance(artifact_payload["proof_bytes"], str):
                artifact_payload["proof_bytes"] = base64.b64decode(artifact_payload["proof_bytes"], validate=True)
            artifact = ProofArtifact.model_validate(artifact_payload)
        except Exception:
            return False, "malformed_proof"
        artifact = artifact.model_copy(update={"public_inputs": public_inputs})
        return (True, "verified") if self._verifier.verify(artifact) else (False, "proof_mismatch")