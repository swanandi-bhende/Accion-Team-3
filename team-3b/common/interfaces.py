"""Abstract base classes defining the core interfaces for the subsystem."""

from abc import ABC, abstractmethod


class StorageBackend(ABC):
    @abstractmethod
    async def upload(self, data: bytes) -> str:
        """Uploads data and returns a Content Identifier (CID)."""
        pass

    @abstractmethod
    async def retrieve(self, cid: str) -> bytes:
        """Retrieves data by Content Identifier (CID)."""
        pass

class Enclave(ABC):
    @property
    @abstractmethod
    def measurement(self) -> str:
        pass

    @property
    @abstractmethod
    def signer_id(self) -> str:
        pass

    @abstractmethod
    def decrypt(self, encrypted_data: bytes, sealed_key: bytes) -> bytes:
        pass

    @abstractmethod
    async def run_isolated(self, model: bytes, input_data: bytes) -> bytes:
        pass

    @abstractmethod
    def secure_erase(self, data: bytes) -> None:
        pass

    @abstractmethod
    def sign(self, data: bytes) -> bytes:
        pass

class ProofResolver(ABC):
    @abstractmethod
    async def resolve(self, reference: str) -> bytes:
        """Resolves a proof reference (e.g., tx_hash or CID) to its bytes."""
        pass

class TEEVerifier(ABC):
    @abstractmethod
    async def verify(self, proof_bytes: bytes, public_inputs: dict) -> tuple[bool, str]:
        """Verifies a TEE attestation report. Returns (is_valid, reason_code)."""
        pass

class ZkMLVerifier(ABC):
    @abstractmethod
    async def verify(self, proof_bytes: bytes, public_inputs: dict) -> tuple[bool, str]:
        """Verifies a zkML proof. Returns (is_valid, reason_code)."""
        pass
