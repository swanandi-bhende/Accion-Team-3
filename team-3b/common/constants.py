"""Shared constants for the Verifiable AI Execution subsystem."""

# Supported architectures for zkML proof generation
SUPPORTED_ARCHITECTURES = [
    "linear",
    "cnn",
    "mlp",
    "transformer_small"
]

# Proof submission batching constants
BATCH_WINDOW_S = 10.0
MAX_RETRIES = 3
RETRY_INTERVAL_S = 10.0

# Event listener reconnection bounds
RECONNECT_BASE_S = 1.0
RECONNECT_MAX_S = 60.0

# Model registry upload backoff bounds
UPLOAD_BACKOFF_BASE_S = 5.0
UPLOAD_BACKOFF_MAX_S = 300.0

# TEE Constants
MAX_MODEL_SIZE_MB = 500
DEFAULT_INFERENCE_TIMEOUT_S = 30.0

# Verification Interface
TEE_ATTESTATION_EXPIRY_S = 24 * 60 * 60
SUPPORTED_VERIFICATION_FORMATS = ["tee_attestation", "zkml_snark"]
VERIFICATION_REASON_CODES = [
    "invalid_signature",
    "proof_mismatch",
    "expired_attestation",
    "malformed_proof",
]
