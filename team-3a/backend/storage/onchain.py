"""Records artifact content hashes on-chain after a successful upload (Requirement 6.2)."""
from __future__ import annotations

from dataclasses import dataclass

from backend.storage.backends import StorageBackend, sha256_hex, verify_integrity
from backend.storage.errors import IntegrityError


@dataclass
class ArtifactRecord:
    task_id: str
    content_hash: str
    size_bytes: int


class ArtifactStore:
    """Wires upload -> on-chain record into one flow (Req 6.1, 6.2).

    `recorder` is any callable taking (task_id, content_hash); in production it is a
    bound web3 contract function, in tests a list-append. Keeping it a plain callable
    means this module has no web3 import and stays testable without a chain.
    """

    def __init__(self, backend: StorageBackend, recorder):
        self.backend = backend
        self.recorder = recorder

    async def store(self, task_id: str, data: bytes) -> ArtifactRecord:
        content_hash = await self.backend.upload(data)
        self.recorder(task_id, content_hash)
        return ArtifactRecord(task_id=task_id, content_hash=content_hash, size_bytes=len(data))

    async def load(self, content_hash: str) -> bytes:
        """Retrieves and verifies in one step (Req 6.3, 6.5)."""
        data = await self.backend.retrieve(content_hash)
        # Backends whose content address is not a plain sha256 (IPFS CIDs) supply their
        # own addressing; verifying a CID against sha256 would reject every real artifact.
        address_of = getattr(self.backend, "content_address", None)
        if address_of is not None:
            actual = await address_of(data)
            if actual != content_hash:
                raise IntegrityError(
                    f"integrity check failed: expected {content_hash}, computed {actual}"
                )
            return data
        return verify_integrity(data, content_hash, digest=sha256_hex)
