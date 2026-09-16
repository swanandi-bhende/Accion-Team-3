import hashlib
import time
from enum import Enum
from typing import Optional

from common.exceptions import TEEUnavailableError, IntegrityCheckFailedError
from common.models import AttestationReport
from inference.enclave_sim import EnclaveSim
from common.interfaces import Enclave

class TEEMode(Enum):
    INTEL_SGX = "sgx"
    AMD_SEV = "sev"

class TEERuntime:
    def __init__(self, mode_str: str):
        try:
            self.mode = TEEMode(mode_str.lower())
        except ValueError:
            self.mode = TEEMode.INTEL_SGX # default

        self.enclave: Optional[Enclave] = None
        self._initialized = False

    def _create_enclave(self) -> Enclave:
        """Adapter switch for TEE initialization."""
        print(f"WARNING: TEE_MODE={self.mode.value} requested but running in SIMULATION — not hardware-attested")
        # In the future, branch here for SGXEnclave vs SEVEnclave
        return EnclaveSim()

    def initialize(self):
        self.enclave = self._create_enclave()
        self._initialized = True

    def shutdown(self):
        self.enclave = None
        self._initialized = False

    def _check_initialized(self):
        if not self._initialized or not self.enclave:
            raise TEEUnavailableError("TEERuntime is not initialized")

    async def execute_inference(self, model_binary: bytes, model_hash: str, input_data: bytes, sealed_key: bytes) -> bytes:
        self._check_initialized()
        
        # Check size constraint (Requirement 8.6) 500MB
        if len(model_binary) > 500 * 1024 * 1024:
            raise ValueError("Model binary exceeds 500MB limit")

        decrypted_model = None
        try:
            decrypted_model = self.enclave.decrypt(model_binary, sealed_key)
            computed_hash = hashlib.sha256(decrypted_model).hexdigest()
            
            if computed_hash != model_hash:
                raise IntegrityCheckFailedError(computed_hash, model_hash)

            output = await self.enclave.run_isolated(decrypted_model, input_data)
            return output
        finally:
            if decrypted_model is not None:
                self.enclave.secure_erase(decrypted_model)

    def generate_attestation(self, input_data: bytes, output: bytes) -> AttestationReport:
        self._check_initialized()

        input_hash = hashlib.sha256(input_data).hexdigest()
        output_hash = hashlib.sha256(output).hexdigest()
        
        payload_to_sign = f"{self.enclave.measurement}:{self.enclave.signer_id}:{input_hash}:{output_hash}".encode('utf-8')
        signature = self.enclave.sign(payload_to_sign)

        return AttestationReport(
            enclave_measurement=self.enclave.measurement,
            signer_identity=self.enclave.signer_id,
            input_hash=input_hash,
            output_hash=output_hash,
            hardware_signature=signature,
            timestamp=time.time()
        )
