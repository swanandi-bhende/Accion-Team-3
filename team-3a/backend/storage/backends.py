"""Decentralized storage integration (Requirement 6).

The `StorageBackend` protocol keeps the on-chain recording layer independent of which
network is configured; `IPFSBackend` is the concrete implementation, `InMemoryBackend`
is used by the tests so the suite does not need a live IPFS node.
"""
from __future__ import annotations

import hashlib
from typing import Protocol

import httpx

from backend.config import settings
from backend.storage.errors import IntegrityError, StorageUnavailableError


class StorageBackend(Protocol):
    async def upload(self, data: bytes) -> str:
        """Stores `data` and returns its content address."""

    async def retrieve(self, content_hash: str) -> bytes:
        """Fetches the artifact stored at `content_hash`."""


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def verify_integrity(data: bytes, expected_hash: str, *, digest=sha256_hex) -> bytes:
    """Requirement 6.5. Raises IntegrityError when the payload does not match.

    Kept as a free function so it can be applied to any backend's output, including
    ones whose content address is not a plain sha256 of the bytes.
    """
    actual = digest(data)
    if actual != expected_hash:
        raise IntegrityError(
            f"integrity check failed: expected {expected_hash}, computed {actual}"
        )
    return data


class IPFSBackend:
    """Talks to a Kubo HTTP API (`/api/v0/add`, `/api/v0/cat`)."""

    def __init__(
        self,
        api_url: str | None = None,
        timeout: float | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.api_url = (api_url or settings.ipfs_api_url).rstrip("/")
        self.timeout = timeout if timeout is not None else settings.storage_timeout_seconds
        self._transport = transport  # test seam: lets tests fake a Kubo node

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=self.timeout, transport=self._transport)

    async def content_address(self, data: bytes) -> str:
        """The CID IPFS would assign to `data`, computed without storing it.

        An IPFS CID is not a plain sha256 of the bytes (it hashes a UnixFS-wrapped,
        possibly chunked DAG), so Req 6.5's "compute the hash and compare" has to use the
        node's own addressing. `only-hash` asks Kubo for exactly that and stores nothing.
        """
        try:
            async with self._client() as client:
                response = await client.post(
                    f"{self.api_url}/api/v0/add",
                    params={"only-hash": "true"},
                    files={"file": ("artifact", data)},
                )
                response.raise_for_status()
                return response.json()["Hash"]
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise StorageUnavailableError(f"IPFS unreachable: {exc}") from exc

    async def upload(self, data: bytes) -> str:
        try:
            async with self._client() as client:
                response = await client.post(
                    f"{self.api_url}/api/v0/add",
                    files={"file": ("artifact", data)},
                )
                response.raise_for_status()
                return response.json()["Hash"]
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            # Req 6.4: an unreachable network surfaces as StorageUnavailable, not a
            # raw transport error, and never hangs past the configured timeout.
            raise StorageUnavailableError(f"IPFS unreachable: {exc}") from exc

    async def retrieve(self, content_hash: str) -> bytes:
        try:
            async with self._client() as client:
                response = await client.post(
                    f"{self.api_url}/api/v0/cat", params={"arg": content_hash}
                )
                response.raise_for_status()
                return response.content
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise StorageUnavailableError(f"IPFS unreachable: {exc}") from exc


class InMemoryBackend:
    """Content-addressed store backed by a dict. Test double for IPFSBackend."""

    def __init__(self) -> None:
        self._store: dict[str, bytes] = {}

    async def upload(self, data: bytes) -> str:
        content_hash = sha256_hex(data)
        self._store[content_hash] = data
        return content_hash

    async def retrieve(self, content_hash: str) -> bytes:
        if content_hash not in self._store:
            raise KeyError(content_hash)
        return self._store[content_hash]

    def tamper(self, content_hash: str, replacement: bytes) -> None:
        """Corrupts stored bytes so integrity verification can be exercised."""
        self._store[content_hash] = replacement
