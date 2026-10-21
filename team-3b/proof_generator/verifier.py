"""Independent proof verification boundary used by the verification service."""

import hashlib
import hmac
import json
from typing import Any

from common.constants import SUPPORTED_ARCHITECTURES
from common.models import ProofArtifact

BACKEND_NAME = "development-commitment"


def commitment_proof(public_inputs: dict[str, Any], architecture: str) -> bytes:
    """Bind ``public_inputs`` and the circuit for ``architecture`` into proof bytes."""
    canonical_inputs = json.dumps(public_inputs, sort_keys=True, separators=(",", ":")).encode()
    circuit_ref = f"{BACKEND_NAME}:circuit:{architecture}:v1"
    return (
        b"DEVCOMMITMENT1"
        + hashlib.sha256(canonical_inputs).digest()
        + hashlib.sha256(circuit_ref.encode()).digest()
    )


class ProofVerifier:
    """Verify proof artifacts without calling the prover implementation."""

    def verify(self, artifact: ProofArtifact) -> bool:
        """Check that the artifact's commitment matches its public inputs.

        Rejects artifacts with an unsupported architecture, or with a verification
        key that does not end in ``/<architecture>.vk``. This checks the development
        commitment, not a zero-knowledge proof.
        """
        public_inputs = artifact.public_inputs
        architecture = public_inputs.get("architecture")
        if architecture not in SUPPORTED_ARCHITECTURES:
            return False
        verification_key_ref = artifact.verification_key_ref
        if not verification_key_ref.endswith(f"/{architecture}.vk"):
            return False
        return hmac.compare_digest(artifact.proof_bytes, commitment_proof(public_inputs, architecture))
