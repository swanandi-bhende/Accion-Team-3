import asyncio
import hashlib
import json
import logging
import os
import uuid
from collections.abc import Awaitable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import cast

from redis.asyncio import Redis

from common.exceptions import IntegrityCheckFailedError, StorageUnavailableError
from common.interfaces import StorageBackend
from common.models import ModelEntry

logger = logging.getLogger(__name__)

UPLOAD_QUEUE_KEY = "model_registry:upload_queue"
FAILED_UPLOAD_KEY = "model_registry:upload_failed"
DEFAULT_SPOOL_DIR = Path(__file__).resolve().parents[1] / "data" / "upload_spool"


@dataclass(frozen=True)
class PendingUpload:
    """A model binary waiting to reach storage. Its bytes live in ``spool_path``."""

    upload_id: str
    model_id: str
    architecture: str | None
    model_hash: str
    spool_path: str


class ModelRegistry:
    """Redis-backed registry that maps model IDs to versioned storage CIDs.

    Each version is a Redis hash holding its CID and hash, and a ``latest`` key points
    at the newest version. Model bytes live in the storage backend. ``register``
    re-reads them from storage and checks them before recording a version.

    Uploads follow Requirement 7.5: a binary is spooled to disk and queued in Redis
    before the first attempt, then retried with exponential backoff (5 s doubling to
    300 s) for as long as storage is unreachable. There is no attempt cap. A queued
    upload survives a restart, and ``resume_uploads`` finishes it.
    """

    UPLOAD_BACKOFF_BASE = 5.0
    UPLOAD_BACKOFF_MAX = 300.0

    def __init__(
        self,
        redis_client: Redis,
        storage_backend: StorageBackend,
        spool_dir: Path = DEFAULT_SPOOL_DIR,
    ):
        self.redis = redis_client
        self.storage = storage_backend
        self.spool_dir = spool_dir

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
        model_hash: str | None = None,
    ) -> None:
        """Record a version whose CID resolves to ``model_binary``.

        ``model_binary`` is the stored artifact: plaintext for a plain model, or the sealed
        blob for an encrypted one. ``model_hash`` is the SHA-256 the TEE checks after
        decryption, so for a sealed model it is the hash of the plaintext. It defaults
        to the hash of ``model_binary``.

        Raises:
            StorageUnavailableError: If storage cannot be reached to re-read the CID.
                The caller should retry, not treat the model as tampered.
            IntegrityCheckFailedError: If the CID resolves to bytes other than
                ``model_binary``.
        """
        try:
            resolved_binary = await self.storage.retrieve(cid)
        except StorageUnavailableError:
            raise
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
            "hash": model_hash or expected_hash,
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
        model_hash: str | None = None,
    ) -> str:
        """Store ``model_binary`` in storage and register it; returns the CID.

        The binary is queued durably before the first attempt. The call returns once the
        upload has completed. While storage is unreachable it waits, retrying with
        exponential backoff, so a long outage is visible as a pending upload rather than
        a lost model.
        """
        pending = self._spool(model_id, model_binary, architecture, model_hash)
        await cast(Awaitable[int], self.redis.lpush(UPLOAD_QUEUE_KEY, _encode(pending)))
        return await self._complete(pending, model_binary)

    async def resume_uploads(self) -> list[str]:
        """Finish every queued upload, for example after a restart. Returns the CIDs."""
        cids = []
        queued = await cast(Awaitable[list[bytes]], self.redis.lrange(UPLOAD_QUEUE_KEY, 0, -1))
        for raw in queued:
            pending = PendingUpload(**json.loads(raw))
            binary = Path(pending.spool_path).read_bytes()
            cids.append(await self._complete(pending, binary))
        return cids

    async def pending_upload_count(self) -> int:
        """How many uploads are queued and not yet in storage."""
        return int(await cast(Awaitable[int], self.redis.llen(UPLOAD_QUEUE_KEY)))

    def _spool(
        self,
        model_id: str,
        model_binary: bytes,
        architecture: str | None,
        model_hash: str | None,
    ) -> PendingUpload:
        """Write the binary to disk and fsync it, so the queue entry never outlives its bytes."""
        self.spool_dir.mkdir(parents=True, exist_ok=True)
        upload_id = uuid.uuid4().hex
        path = self.spool_dir / f"{upload_id}.bin"
        with path.open("wb") as spooled:
            spooled.write(model_binary)
            spooled.flush()
            os.fsync(spooled.fileno())
        return PendingUpload(
            upload_id=upload_id,
            model_id=model_id,
            architecture=architecture,
            model_hash=model_hash or hashlib.sha256(model_binary).hexdigest(),
            spool_path=str(path),
        )

    async def _complete(self, pending: PendingUpload, model_binary: bytes) -> str:
        """Retry until storage accepts the binary and the registry records it."""
        delay = self.UPLOAD_BACKOFF_BASE
        attempt = 0
        while True:
            attempt += 1
            try:
                cid = await self.storage.upload(model_binary)
                await self.register(
                    pending.model_id,
                    cid,
                    model_binary,
                    architecture=pending.architecture,
                    model_hash=pending.model_hash,
                )
            except StorageUnavailableError as error:
                if attempt % 10 == 0:
                    logger.error(
                        "MODEL_UPLOAD_STILL_PENDING model_id=%s attempts=%s error=%s",
                        pending.model_id,
                        attempt,
                        error,
                    )
                else:
                    logger.warning(
                        "MODEL_UPLOAD_RETRY model_id=%s attempt=%s delay_s=%s error=%s",
                        pending.model_id,
                        attempt,
                        delay,
                        error,
                    )
                await asyncio.sleep(delay)
                delay = min(delay * 2, self.UPLOAD_BACKOFF_MAX)
                continue
            except IntegrityCheckFailedError:
                # The CID resolves to other bytes. Retrying cannot fix that, so the
                # upload moves to the failed list and the spool file is kept for inspection.
                await cast(Awaitable[int], self.redis.lrem(UPLOAD_QUEUE_KEY, 1, _encode(pending)))
                await cast(Awaitable[int], self.redis.lpush(FAILED_UPLOAD_KEY, _encode(pending)))
                logger.error("MODEL_UPLOAD_INTEGRITY_FAILURE model_id=%s", pending.model_id)
                raise
            await cast(Awaitable[int], self.redis.lrem(UPLOAD_QUEUE_KEY, 1, _encode(pending)))
            Path(pending.spool_path).unlink(missing_ok=True)
            return cid


def _encode(pending: PendingUpload) -> str:
    return json.dumps(asdict(pending), sort_keys=True)
