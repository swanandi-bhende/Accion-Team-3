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


class ConfigurationError(Exception):
    """Raised when required environment configuration is missing or invalid.

    The message is itemized: every offending variable is listed at once so a
    misconfigured deployment can be fixed in one pass instead of one variable at
    a time.
    """


class UnsupportedFormatError(Exception):
    """Raised when verification receives an unsupported proof format."""

    def __init__(self, proof_format: str, supported: list[str]):
        self.proof_format = proof_format
        self.supported = supported
        super().__init__(f"Unsupported proof format: {proof_format}. Supported: {supported}")
