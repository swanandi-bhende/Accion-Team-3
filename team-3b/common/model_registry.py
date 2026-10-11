import asyncio
import hashlib
from collections.abc import Awaitable
from typing import cast

from redis.asyncio import Redis

from common.exceptions import IntegrityCheckFailedError, StorageUnavailableError
from common.interfaces import StorageBackend
from common.models import ModelEntry


class ModelRegistry:
    UPLOAD_BACKOFF_BASE = 5.0
    UPLOAD_BACKOFF_MAX = 300.0

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
        """Upload to decentralized storage with exponential backoff on failure."""
        delay = self.UPLOAD_BACKOFF_BASE
        while True:
            try:
                cid = await self.storage.upload(model_binary)
                await self.register(model_id, cid, model_binary, architecture=architecture)
                return cid
            except StorageUnavailableError:
                await asyncio.sleep(delay)
                delay = min(delay * 2, self.UPLOAD_BACKOFF_MAX)
