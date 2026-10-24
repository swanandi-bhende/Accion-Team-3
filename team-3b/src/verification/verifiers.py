"""Concrete TEE and zkML verification implementations."""

import asyncio
import base64
import json
import time
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from common.constants import EZKL_PROOF_BACKEND, TEE_ATTESTATION_EXPIRY_S
from common.exceptions import (
    ProvingBackendUnavailableError,
    ProvingKeysUnavailableError,
)
from common.models import AttestationReport, ProofArtifact
from proof_generator import ezkl_backend
from proof_generator.verifier import ProofVerifier


def serialize_attestation(report: AttestationReport, signer_public_key: bytes) -> bytes:
    """Serialize an attestation report and its signer key for the TEE verifier.

    The hardware signature is base64-encoded. The output is compact, key-sorted
    JSON that ``ConcreteTEEVerifier.verify`` can parse.
    """
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


def serialize_proof_artifact(artifact: ProofArtifact) -> bytes:
    """Serialize a proof artifact to the wire format the zkML verifier accepts."""
    payload = {
        "proof_bytes": base64.b64encode(artifact.proof_bytes).decode(),
        "public_inputs": artifact.public_inputs,
        "verification_key_ref": artifact.verification_key_ref,
        "backend": artifact.backend,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()


class ConcreteTEEVerifier:
    """Check TEE attestation proofs against trusted enclave signer keys.

    An attestation passes only if it is recent (no older than ``expiry_s``), signed
    by the key trusted for its signer identity, and bound to the input and output
    hashes in the public inputs.
    """

    def __init__(self, trusted_public_keys: dict[str, bytes], expiry_s: float = TEE_ATTESTATION_EXPIRY_S):
        self.trusted_public_keys = trusted_public_keys
        self.expiry_s = expiry_s

    async def verify(self, proof_bytes: bytes, public_inputs: dict[str, Any]) -> tuple[bool, str]:
        """Verify a serialized attestation against the public inputs.

        Returns:
            A ``(passed, reason_code)`` tuple. The reason is ``verified`` on success,
            otherwise ``malformed_proof``, ``expired_attestation``, ``invalid_signature``,
            or ``proof_mismatch``.
        """
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
    """Verify serialized zkML proof artifacts.

    An EZKL proof is checked with the proving bundle stored for its model in
    ``zkml_store``. It is zero-knowledge, and it proves the model's circuit ran on some
    input. A development-commitment artifact is checked with the development verifier,
    which is not zero-knowledge and is never submitted on chain.
    """

    def __init__(self, zkml_store: Path | None = None) -> None:
        self._verifier = ProofVerifier()
        self._zkml_store = zkml_store

    async def verify(self, proof_bytes: bytes, public_inputs: dict[str, Any]) -> tuple[bool, str]:
        """Verify a serialized proof artifact against the supplied public inputs.

        The artifact's own public inputs are replaced with the caller's before checking,
        so the proof must match them.

        Returns:
            A ``(passed, reason_code)`` tuple: ``verified``, ``malformed_proof``, or
            ``proof_mismatch``.
        """
        try:
            artifact_payload = json.loads(proof_bytes)
            if "proof_bytes" in artifact_payload and isinstance(artifact_payload["proof_bytes"], str):
                artifact_payload["proof_bytes"] = base64.b64decode(artifact_payload["proof_bytes"], validate=True)
            artifact = ProofArtifact.model_validate(artifact_payload)
        except Exception:
            return False, "malformed_proof"
        artifact = artifact.model_copy(update={"public_inputs": public_inputs})
        if artifact.backend == EZKL_PROOF_BACKEND:
            passed = await asyncio.to_thread(self._verify_ezkl, artifact)
            return (True, "verified") if passed else (False, "proof_mismatch")
        return (True, "verified") if self._verifier.verify(artifact) else (False, "proof_mismatch")

    def _verify_ezkl(self, artifact: ProofArtifact) -> bool:
        if self._zkml_store is None:
            return False
        public_inputs = artifact.public_inputs
        model_hash = public_inputs.get("model_hash")
        # Hex only: the hash names a directory in the store, so nothing else may reach the path.
        if not isinstance(model_hash, str) or not model_hash.isalnum():
            return False
        try:
            bundle = ezkl_backend.load_bundle(self._zkml_store, model_hash)
            instances_match = ezkl_backend.instances_hash(artifact.proof_bytes) == public_inputs.get(
                "zk_instances_hash"
            )
        except (ProvingKeysUnavailableError, OSError, ValueError, KeyError, TypeError):
            return False
        if not instances_match or bundle.architecture != public_inputs.get("architecture"):
            return False
        try:
            return ezkl_backend.verify(bundle, artifact.proof_bytes)
        except ProvingBackendUnavailableError:
            return False
