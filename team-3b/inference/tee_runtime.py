import hashlib
import logging
import time
from enum import Enum

from cryptography.exceptions import InvalidTag

from common.exceptions import IntegrityCheckFailedError, TEEUnavailableError
from common.interfaces import Enclave
from common.models import AttestationReport
from inference.enclave_sim import EnclaveSim

logger = logging.getLogger(__name__)
# Requirement 8.6: the largest model binary the enclave accepts.
MAX_MODEL_BYTES = 500 * 1024 * 1024


class TEEMode(Enum):
    """TEE hardware families selectable through the TEE_MODE setting."""

    INTEL_SGX = "sgx"
    AMD_SEV = "sev"

class TEERuntime:
    """Own the enclave used for inference and attestation.

    Only the simulated enclave is implemented. An unrecognised mode string falls back to SGX.
    """

    def __init__(self, mode_str: str):
        try:
            self.mode = TEEMode(mode_str.lower())
        except ValueError:
            self.mode = TEEMode.INTEL_SGX # default

        self.enclave: Enclave | None = None

    def _create_enclave(self) -> Enclave:
        """Adapter switch for TEE initialization."""
        logger.warning("TEE_MODE=%s requested but running in SIMULATION; not hardware-attested", self.mode.value)
        # In the future, branch here for SGXEnclave vs SEVEnclave
        return EnclaveSim()

    @property
    def initialized(self) -> bool:
        """Whether an enclave is currently attached."""
        return self.enclave is not None

    def initialize(self) -> None:
        """Create the enclave. In this build that is always the simulator."""
        self.enclave = self._create_enclave()

    def shutdown(self) -> None:
        """Release the enclave. Inference and attestation fail until initialize() runs again."""
        self.enclave = None

    def _require_enclave(self) -> Enclave:
        if self.enclave is None:
            raise TEEUnavailableError("TEERuntime is not initialized")
        return self.enclave

    async def execute_inference(self, model_binary: bytes, model_hash: str, input_data: bytes, sealed_key: bytes) -> bytes:
        """Verify the model against model_hash, decrypting it if needed, then run it in the enclave.

        The model may be plaintext or an AES-GCM encrypted blob. If its SHA-256 does not
        match model_hash as given, it is decrypted with sealed_key and checked again.
        The decrypted copy is passed to secure_erase after the run.

        Args:
            model_binary: Model bytes, either plaintext or an encrypted blob.
            model_hash: Expected SHA-256 hex digest of the plaintext model.
            input_data: Input passed to the model.
            sealed_key: Key used to decrypt an encrypted model.

        Returns:
            The output produced by the enclave.

        Raises:
            TEEUnavailableError: If the runtime has not been initialized.
            ValueError: If the model exceeds MAX_MODEL_BYTES or decryption rejects its input.
            IntegrityCheckFailedError: If the model does not match model_hash, or the
                AEAD tag fails to verify.
        """
        enclave = self._require_enclave()

        if len(model_binary) > MAX_MODEL_BYTES:
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
        """Sign a report that binds the input and output hashes to this enclave.

        Args:
            input_data: Inference input. Only its SHA-256 is signed.
            output: Inference output. Only its SHA-256 is signed.

        Returns:
            An AttestationReport with the enclave measurement, signer identity, both
            hashes and the signature.

        Raises:
            TEEUnavailableError: If the runtime has not been initialized.
        """
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
