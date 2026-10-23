"""Abstract base classes defining the core interfaces for the subsystem."""

from abc import ABC, abstractmethod


class StorageBackend(ABC):
    """Abstract interface for content-addressed storage of model binaries.

    ``upload`` returns a Content Identifier (CID) that ``retrieve`` accepts later.
    ``ModelRegistry`` uses it to store model binaries and to check them on registration.
    """

    @abstractmethod
    async def upload(self, data: bytes) -> str:
        """Uploads data and returns a Content Identifier (CID)."""
        pass

    @abstractmethod
    async def retrieve(self, cid: str) -> bytes:
        """Retrieves data by Content Identifier (CID)."""
        pass

class Enclave(ABC):
    """Abstract trusted execution environment that runs model inference.

    Covers the enclave's attestation identity, decryption of sealed model bytes,
    isolated execution, memory wiping and signing. ``EnclaveSim`` is the local
    development implementation and gives no hardware guarantees.
    """

    @property
    @abstractmethod
    def measurement(self) -> str:
        """Identifier of the code loaded in the enclave, included in attestations."""
        pass

    @property
    @abstractmethod
    def signer_id(self) -> str:
        """Identifier of the key that signs attestation reports."""
        pass

    @abstractmethod
    def decrypt(self, encrypted_data: bytes, sealed_key: bytes) -> bytes:
        """Decrypt a model blob that was sealed for this enclave."""
        pass

    @abstractmethod
    async def run_isolated(self, model: bytes, input_data: bytes) -> bytes:
        """Run the model on the input in an isolated context and return the output.

        Args:
            model: Model binary, already decrypted.
            input_data: Raw input payload for the model.
        """
        pass

    @abstractmethod
    def secure_erase(self, data: bytes) -> None:
        """Wipe a sensitive buffer, such as decrypted model bytes, from memory.

        A real enclave overwrites the buffer in place. ``EnclaveSim`` only zeroizes
        a copy, so the caller's bytes object is left unchanged.
        """
        pass

    @abstractmethod
    def sign(self, data: bytes) -> bytes:
        """Sign data with the enclave key so attestation reports can be verified."""
        pass
