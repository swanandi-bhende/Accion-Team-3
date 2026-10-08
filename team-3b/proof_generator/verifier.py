"""Independent proof verification boundary used by the verification service."""

import hashlib
import hmac
import json

from common.constants import SUPPORTED_ARCHITECTURES
from common.models import ProofArtifact


class ProofVerifier:
    """Verify proof artifacts without calling the prover implementation."""

    BACKEND_NAME = "development-commitment"

    def verify(self, artifact: ProofArtifact) -> bool:
        public_inputs = artifact.public_inputs
        architecture = public_inputs.get("architecture")
        if architecture not in SUPPORTED_ARCHITECTURES:
            return False
        verification_key_ref = artifact.verification_key_ref
        if not verification_key_ref.endswith(f"/{architecture}.vk"):
            return False

        canonical_inputs = json.dumps(public_inputs, sort_keys=True, separators=(",", ":")).encode()
        expected = (
            b"DEVCOMMITMENT1"
            + hashlib.sha256(canonical_inputs).digest()
            + hashlib.sha256(f"{self.BACKEND_NAME}:circuit:{architecture}:v1".encode()).digest()
        )
        return hmac.compare_digest(artifact.proof_bytes, expected)
