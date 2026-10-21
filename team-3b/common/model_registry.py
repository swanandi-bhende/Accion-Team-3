import asyncio
import hashlib
import logging
from collections.abc import Awaitable
from typing import cast

from redis.asyncio import Redis

from common.exceptions import IntegrityCheckFailedError, StorageUnavailableError
from common.interfaces import StorageBackend
from common.models import ModelEntry

logger = logging.getLogger(__name__)


class ModelRegistry:
    """Redis-backed registry that maps model IDs to versioned storage CIDs.

    Each version is a Redis hash holding its CID and SHA-256 hash, and a ``latest``
    key points at the newest version. Model bytes live in the storage backend.
    ``register`` re-reads them from storage and checks the hash before recording a version.
    """

    UPLOAD_BACKOFF_BASE = 5.0
    UPLOAD_BACKOFF_MAX = 300.0
    # With the backoff above, six attempts wait about 155 s in total before failing.
    MAX_UPLOAD_ATTEMPTS = 6

    def __init__(self, redis_client: Redis, storage_backend: StorageBackend):
        self.redis = redis_client
        self.storage = storage_backend

    async def get(self, model_id: str, version: int | None = None) -> ModelEntry | None:
        """Resolve model by ID (latest version if version is None)."""
        if version is None:
            latest_key = f"model:{model_id}:latest"
            target_key = await self.redis.get(latest_key)
            if not target_key:
                return None
            target_key = target_key.decode() if isinstance(target_key, bytes) else target_key
        else:
            target_key = f"model:{model_id}:v{version}"

        data = await cast(
            Awaitable[dict[bytes | str, bytes | str]], self.redis.hgetall(target_key)
        )
        if not data:
            return None

        decoded = {k.decode('utf-8') if isinstance(k, bytes) else k: v.decode('utf-8') if isinstance(v, bytes) else v for k, v in data.items()}

        return ModelEntry(
            model_id=model_id,
            version=int(decoded["version"]),
            cid=decoded["cid"],
            hash=decoded["hash"],
            architecture=decoded.get("architecture"),
        )

    async def _next_version(self, model_id: str) -> int:
        """Atomic counter for the next version using Redis INCR."""
        counter_key = f"model:{model_id}:version_counter"
        return int(await self.redis.incr(counter_key))

    async def register(
        self,
        model_id: str,
        cid: str,
        model_binary: bytes,
        architecture: str | None = None,
    ) -> None:
        """Register model with CID after validating it resolves correctly."""

        try:
            resolved_binary = await self.storage.retrieve(cid)
        except Exception as e:
            raise IntegrityCheckFailedError(f"retrieve_error_{cid}", "expected_binary") from e

        computed_hash = hashlib.sha256(resolved_binary).hexdigest()
        expected_hash = hashlib.sha256(model_binary).hexdigest()

        if computed_hash != expected_hash:
            raise IntegrityCheckFailedError(computed_hash, expected_hash)

        version = await self._next_version(model_id)

        key = f"model:{model_id}:v{version}"
        latest_key = f"model:{model_id}:latest"

        pipeline = self.redis.pipeline()
        metadata = {
            "version": str(version),
            "cid": cid,
            "hash": expected_hash
        }
        if architecture is not None:
            metadata["architecture"] = architecture
        pipeline.hset(key, mapping=metadata)
        pipeline.set(latest_key, key)
        await pipeline.execute()

    async def upload_model(
        self,
        model_id: str,
        model_binary: bytes,
        architecture: str | None = None,
    ) -> str:
        """Upload to decentralized storage, backing off exponentially between attempts.

        Gives up after ``MAX_UPLOAD_ATTEMPTS`` and re-raises ``StorageUnavailableError``,
        so a prolonged storage outage fails the caller instead of hanging it forever.
        """
        delay = self.UPLOAD_BACKOFF_BASE
        attempt = 0
        while True:
            attempt += 1
            try:
                cid = await self.storage.upload(model_binary)
                await self.register(model_id, cid, model_binary, architecture=architecture)
                return cid
            except StorageUnavailableError as error:
                if attempt >= self.MAX_UPLOAD_ATTEMPTS:
                    logger.error("MODEL_UPLOAD_FAILED model_id=%s attempts=%s", model_id, attempt)
                    raise
                logger.warning(
                    "MODEL_UPLOAD_RETRY model_id=%s attempt=%s delay_s=%s error=%s",
                    model_id,
                    attempt,
                    delay,
                    error,
                )
                await asyncio.sleep(delay)
                delay = min(delay * 2, self.UPLOAD_BACKOFF_MAX)
