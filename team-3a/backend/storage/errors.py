"""Storage error types (Requirement 6)."""


class StorageError(Exception):
    """Base class for storage failures."""


class StorageUnavailableError(StorageError):
    """The decentralized storage network could not be reached in time (Req 6.4)."""

    status_code = "StorageUnavailable"


class IntegrityError(StorageError):
    """A retrieved artifact did not hash to its content address (Req 6.5)."""

    status_code = "IntegrityCheckFailed"
