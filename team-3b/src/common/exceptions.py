"""Custom exceptions for the Verifiable AI Execution subsystem."""

class ModelNotFoundError(Exception):
    """Raised when a requested model is not found in the registry."""
    def __init__(self, model_id: str):
        self.model_id = model_id
        super().__init__(f"Model not found: {model_id}")

class TimeoutExceededError(Exception):
    """Raised when an inference execution exceeds the configured timeout."""
    def __init__(self, timeout: float):
        self.timeout = timeout
        super().__init__(f"Execution timeout exceeded ({timeout}s)")

class TEEUnavailableError(Exception):
    """Raised when TEE hardware is unavailable or uninitialized."""
    pass

class IntegrityCheckFailedError(Exception):
    """Raised when a model binary fails integrity verification."""
    def __init__(self, computed_hash: str, expected_hash: str):
        self.computed_hash = computed_hash
        self.expected_hash = expected_hash
        super().__init__(f"Integrity check failed. Expected {expected_hash}, got {computed_hash}")

class UnsupportedArchitectureError(Exception):
    """Raised when zkML proof generation is requested for an unsupported architecture."""
    def __init__(self, architecture: str, supported: list[str]):
        self.architecture = architecture
        self.supported = supported
        super().__init__(f"Unsupported architecture: {architecture}. Supported: {supported}")

class StorageUnavailableError(Exception):
    """Raised when the decentralized storage backend is unreachable."""
    pass


class ProvingBackendUnavailableError(Exception):
    """The EZKL toolchain is not installed, so its proofs cannot be made or checked."""


class ProvingKeysUnavailableError(Exception):
    """No proving bundle exists for the model, so its proof cannot be made or checked."""


class ProofInconsistentError(Exception):
    """The zkML circuit's output disagrees with the TEE's output for the same input."""


class ProofGenerationError(Exception):
    """The EZKL toolchain failed to make or build a proof for an input it accepted."""


class ProofNotSubmittableError(Exception):
    """A proof's backend is not allowed on chain, so the proof is not submitted.

    Development-commitment proofs are not zero-knowledge. They reach the chain only when
    a test opts in explicitly.
    """

    def __init__(self, backend: str):
        self.backend = backend
        super().__init__(f"proof from backend '{backend}' is not allowed on chain")


class ModelExecutionError(Exception):
    """The model could not run on the input.

    Raised for bytes that are not a runnable ONNX model, an input whose shape or dtype
    does not match the model, and runtime failures inside the inference worker.
    """


class ConfigurationError(Exception):
    """Raised when required environment configuration is missing or invalid.

    The message is itemized: every offending variable is listed at once so a
    misconfigured deployment can be fixed in one pass instead of one variable at
    a time.
    """


class ChainMismatchError(ConfigurationError):
    """Raised when an RPC endpoint serves a chain other than the configured one.

    Guarding on the chain id (rather than trusting a URL) is what stops a stack
    that is configured for Team A's Galileo deployment from quietly submitting
    proofs to a local Hardhat node.
    """

    def __init__(self, service: str, expected: int, actual: int, rpc_url: str | None = None):
        self.service = service
        self.expected = expected
        self.actual = actual
        self.rpc_url = rpc_url
        target = f"{rpc_url} " if rpc_url else ""
        super().__init__(
            f"{service}: {target}serves chain id {actual}, but WEB3_CHAIN_ID is {expected}. "
            "Check WEB3_RPC_URL/WEB3_WS_URL, or unset WEB3_CHAIN_ID to accept any chain."
        )


class UnsupportedFormatError(Exception):
    """Raised when verification receives an unsupported proof format."""

    def __init__(self, proof_format: str, supported: list[str]):
        self.proof_format = proof_format
        self.supported = supported
        super().__init__(f"Unsupported proof format: {proof_format}. Supported: {supported}")
