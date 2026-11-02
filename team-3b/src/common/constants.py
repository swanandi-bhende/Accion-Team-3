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

# The EZKL backend (zkonduit/ezkl). Its proofs are zero-knowledge, so they may be
# submitted on chain. Its bundles hold the proving keys and are loaded from the store.
EZKL_PROOF_BACKEND = "ezkl"
# Proving backends whose proofs are zero-knowledge and may be submitted on chain.
PRODUCTION_PROOF_BACKENDS: frozenset[str] = frozenset({EZKL_PROOF_BACKEND})
# The commitment backend. It is not zero-knowledge, so it is refused on chain unless a
# test opts in with ProofSubmitter(allow_development_backend=True).
DEVELOPMENT_PROOF_BACKEND = "development-commitment"
# Quantization tolerance for the EZKL output against the TEE output, in units of the
# circuit's fixed-point step (2**-quantization_scale). Measured error is well under one step.
EZKL_OUTPUT_TOLERANCE_STEPS = 16
