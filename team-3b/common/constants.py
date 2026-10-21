"""Shared constants for the Verifiable AI Execution subsystem."""

# Supported architectures for zkML proof generation
SUPPORTED_ARCHITECTURES = [
    "linear",
    "cnn",
    "mlp",
    "transformer_small"
]

# Verification Interface
TEE_ATTESTATION_EXPIRY_S = 24 * 60 * 60
SUPPORTED_VERIFICATION_FORMATS = ["tee_attestation", "zkml_snark"]
