"""Common models and types for the Verifiable AI Execution subsystem."""


import base64
import binascii

from pydantic import BaseModel, field_serializer, field_validator, model_validator


class InferenceRequest(BaseModel):
    """Request to run a registered model on one input payload."""

    model_id: str
    input_data: bytes
    task_id: str | None = None

    @field_validator("input_data", mode="before")
    @classmethod
    def _decode_base64_text(cls, value: object) -> object:
        """Decode ``input_data`` sent as base64 text, the form JSON clients use.

        Raw bytes pass through unchanged, so Python callers can keep passing bytes.
        """
        if isinstance(value, str):
            try:
                return base64.b64decode(value, validate=True)
            except binascii.Error as error:
                raise ValueError("input_data must be base64-encoded text") from error
        return value

class ExecutionReceipt(BaseModel):
    """Record of one inference run, with hashes of its input and output.

    The zkML prover takes a receipt as input when it generates a proof.
    """

    execution_id: str
    model_id: str
    model_cid: str
    input_hash: str
    output_hash: str
    output: bytes
    timestamp: float

    @field_serializer("output", when_used="json")
    def _encode_output_base64(self, value: bytes) -> str:
        """Send ``output`` as base64 text, the same encoding inference inputs use.

        ``.npy`` outputs are binary, so UTF-8 text would not round-trip them.
        """
        return base64.b64encode(value).decode("ascii")

class AttestationReport(BaseModel):
    """Signed statement from an enclave about one inference run.

    Carries the enclave measurement and signer identity, the input and output
    hashes, and the signature over them.
    """

    enclave_measurement: str
    signer_identity: str
    input_hash: str
    output_hash: str
    hardware_signature: bytes
    timestamp: float

class ProofArtifact(BaseModel):
    """zkML proof for one execution, with the public inputs needed to check it.

    ``verification_key_ref`` is a reference to the verification key the verifier uses.
    """

    proof_bytes: bytes
    public_inputs: dict
    verification_key_ref: str
    # Backend that produced the bytes. The proof submitter sends a proof on chain only if
    # its backend is allowed there. An artifact without a backend is refused.
    backend: str = "unknown"


class VerificationRequest(BaseModel):
    """Request asking a verifier to check a proof.

    The proof may be sent inline as ``proof_bytes`` or referenced by ``proof_reference``.
    """

    proof_format: str
    public_inputs: dict
    proof_bytes: bytes | None = None
    proof_reference: str | None = None


class VerificationResponse(BaseModel):
    """Outcome of a proof verification.

    A success carries ``public_inputs`` and no ``reason_code``. A failure carries
    ``reason_code`` and no ``public_inputs``.
    """

    result: bool
    timestamp: float
    public_inputs: dict | None = None
    reason_code: str | None = None

    @model_validator(mode="after")
    def validate_result_fields(self) -> "VerificationResponse":
        """Check that the result fields match the outcome.

        Raises:
            ValueError: If a success lacks ``public_inputs`` or sets ``reason_code``,
                or if a failure sets ``public_inputs`` or lacks ``reason_code``.
        """
        if self.result and (self.public_inputs is None or self.reason_code is not None):
            raise ValueError("successful verification requires public_inputs only")
        if not self.result and (self.public_inputs is not None or self.reason_code is None):
            raise ValueError("failed verification requires reason_code only")
        return self

class ModelEntry(BaseModel):
    """One stored version of a model, as resolved from the registry.

    ``binary`` stays None when the entry comes from ``ModelRegistry.get``, which
    returns only the CID and hash.
    """

    model_id: str
    version: int
    cid: str
    hash: str
    binary: bytes | None = None
    architecture: str | None = None

class FLTaskConfig(BaseModel):
    """Configuration for one federated-learning training task and its aggregation strategy."""

    task_id: str
    num_clients: int
    num_rounds: int
    aggregation_strategy: str = "fedavg"
    round_timeout: float = 300.0
    # How many clients must be *connected* before a round may start. The default
    # (None) means "all of them", which is correct for a fixed in-process roster
    # but wedges a real network run when a participant disconnects: Flower's
    # client manager blocks waiting for the missing client. Networks that must
    # tolerate dropouts (Requirement 4.5) set this below num_clients.
    min_available_clients: int | None = None

    # Strategy-specific tuning, only read by the matching aggregation strategy.
    trim_fraction: float = 0.2
    num_malicious_clients: int = 0
    dp_clip_norm: float = 1.0
    dp_noise_multiplier: float = 1.0
    dp_num_sampled_clients: int | None = None

    @model_validator(mode="after")
    def validate_strategy_parameters(self) -> "FLTaskConfig":
        """Check that the aggregation and differential-privacy tuning values are in range.

        Raises:
            ValueError: If a tuning value is out of range, including
                ``min_available_clients`` outside ``1..num_clients``.
        """
        if not 0 <= self.trim_fraction < 0.5:
            raise ValueError("trim_fraction must be in [0, 0.5)")
        if self.num_malicious_clients < 0:
            raise ValueError("num_malicious_clients must be non-negative")
        if self.dp_clip_norm <= 0 or self.dp_noise_multiplier <= 0:
            raise ValueError("dp_clip_norm and dp_noise_multiplier must be positive")
        if self.min_available_clients is not None and not 1 <= self.min_available_clients <= self.num_clients:
            raise ValueError("min_available_clients must be between 1 and num_clients")
        return self

class RoundProvenance(BaseModel):
    """Provenance record for one federated-learning round.

    The strategies keep these per task in a Redis list.
    """

    round_number: int
    participating_clients: int
    aggregation_strategy: str
    round_duration: float
    resulting_model_hash: str
