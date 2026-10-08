"""Public verification orchestration."""

import time
from typing import Any

from common.constants import SUPPORTED_VERIFICATION_FORMATS
from common.exceptions import UnsupportedFormatError
from common.models import VerificationRequest, VerificationResponse
from verification.resolver import ProofResolutionError


class VerificationInterface:
    def __init__(self, tee_verifier: Any, zkml_verifier: Any, resolver: Any = None):
        self.tee_verifier = tee_verifier
        self.zkml_verifier = zkml_verifier
        self.resolver = resolver

    async def verify(self, request: VerificationRequest) -> VerificationResponse:
        if request.proof_format not in SUPPORTED_VERIFICATION_FORMATS:
            raise UnsupportedFormatError(request.proof_format, SUPPORTED_VERIFICATION_FORMATS)
        proof_bytes = request.proof_bytes
        if proof_bytes is None and request.proof_reference is not None and self.resolver is not None:
            try:
                proof_bytes = await self.resolver.resolve(request.proof_reference)
            except ProofResolutionError:
                return VerificationResponse(result=False, reason_code="malformed_proof", timestamp=time.time())
        if proof_bytes is None:
            return VerificationResponse(result=False, reason_code="malformed_proof", timestamp=time.time())

        verifier = self.tee_verifier if request.proof_format == "tee_attestation" else self.zkml_verifier
        result, reason = await verifier.verify(proof_bytes, request.public_inputs)
        if result:
            return VerificationResponse(result=True, public_inputs=request.public_inputs, timestamp=time.time())
        return VerificationResponse(result=False, reason_code=reason, timestamp=time.time())
