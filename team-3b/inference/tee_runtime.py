import hashlib
import time
from enum import Enum

from cryptography.exceptions import InvalidTag

from common.exceptions import IntegrityCheckFailedError, TEEUnavailableError
from common.interfaces import Enclave
from common.models import AttestationReport
from inference.enclave_sim import EnclaveSim


class TEEMode(Enum):
    INTEL_SGX = "sgx"
    AMD_SEV = "sev"

class TEERuntime:
    def __init__(self, mode_str: str):
        try:
            self.mode = TEEMode(mode_str.lower())
        except ValueError:
            self.mode = TEEMode.INTEL_SGX # default

        self.enclave: Enclave | None = None

    def _create_enclave(self) -> Enclave:
        """Adapter switch for TEE initialization."""
        print(f"WARNING: TEE_MODE={self.mode.value} requested but running in SIMULATION — not hardware-attested")
        # In the future, branch here for SGXEnclave vs SEVEnclave
        return EnclaveSim()

    @property
    def initialized(self) -> bool:
        return self.enclave is not None

    def initialize(self) -> None:
        self.enclave = self._create_enclave()

    def shutdown(self) -> None:
        self.enclave = None

    def _require_enclave(self) -> Enclave:
        if self.enclave is None:
            raise TEEUnavailableError("TEERuntime is not initialized")
        return self.enclave

    async def execute_inference(self, model_binary: bytes, model_hash: str, input_data: bytes, sealed_key: bytes) -> bytes:
        enclave = self._require_enclave()

        # Check size constraint (Requirement 8.6) 500MB.
        if len(model_binary) > 500 * 1024 * 1024:
            raise ValueError("Model binary exceeds 500MB limit")

        decrypted_model = None
        try:
            # The model may already be in plaintext form, or it may be an encrypted blob
            # that must be decrypted before verification. We accept both to preserve the
            # integrity guarantees while keeping the public contract stable.
            computed_hash = hashlib.sha256(model_binary).hexdigest()
            if computed_hash != model_hash:
                try:
                    decrypted_model = enclave.decrypt(model_binary, sealed_key)
                except InvalidTag as error:
                    # An AEAD tag failure means the stored bytes are not the artifact
                    # the registry committed to. That is the same violation as a
                    # plaintext hash mismatch, so it is reported the same way rather
                    # than escaping as a generic inference error.
                    raise IntegrityCheckFailedError(computed_hash, model_hash) from error
                computed_hash = hashlib.sha256(decrypted_model).hexdigest()
                if computed_hash != model_hash:
                    raise IntegrityCheckFailedError(computed_hash, model_hash)
            else:
                decrypted_model = model_binary

            output = await enclave.run_isolated(decrypted_model, input_data)
            return output
        finally:
            if decrypted_model is not None and decrypted_model is not model_binary:
                enclave.secure_erase(decrypted_model)

    def generate_attestation(self, input_data: bytes, output: bytes) -> AttestationReport:
        enclave = self._require_enclave()

        input_hash = hashlib.sha256(input_data).hexdigest()
        output_hash = hashlib.sha256(output).hexdigest()

        payload_to_sign = f"{enclave.measurement}:{enclave.signer_id}:{input_hash}:{output_hash}".encode()
        signature = enclave.sign(payload_to_sign)

        return AttestationReport(
            enclave_measurement=enclave.measurement,
            signer_identity=enclave.signer_id,
            input_hash=input_hash,
            output_hash=output_hash,
            hardware_signature=signature,
            timestamp=time.time()
        )
