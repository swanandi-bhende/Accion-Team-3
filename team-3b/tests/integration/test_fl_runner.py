"""Integration test for the federated-learning runner entrypoint.

Exercises ``runner.main()`` end to end by swapping its two external factories
(Redis and storage) so no live services are required.  The entrypoint now reads
**all** its configuration from :func:`common.config.get_settings` (Step 16.3),
so the test must also set ``MODEL_STORAGE_URL`` / ``REDIS_URL`` via monkeypatch
(or the runner's ``require("redis_url", "model_storage_url")`` will raise.).
"""
from __future__ import annotations

import hashlib
from collections.abc import Awaitable
from typing import cast

import fakeredis.aioredis as fakeredis
import pytest
from redis.asyncio import Redis

import common.config as _common_config
import federated_learning.runner as runner
from common.interfaces import StorageBackend
from common.model_registry import ModelRegistry

FL_TASK_ID = "test_runner_task_id"
FL_MODEL_ID = "test_runner_model_id"
FL_NUM_CLIENTS = 2
FL_NUM_ROUNDS = 2


class InMemoryStorage(StorageBackend):
    def __init__(self) -> None:
        self.store: dict[str, bytes] = {}

    async def upload(self, data: bytes) -> str:
        cid = "Qm" + hashlib.sha256(data).hexdigest()
        self.store[cid] = data
        return cid

    async def retrieve(self, cid: str) -> bytes:
        return self.store[cid]


def _setup_runner_env(monkeypatch: pytest.MonkeyPatch) -> tuple[Redis, InMemoryStorage]:
    """Set env vars and factory overrides that runner.main() needs.

    We call ``get_settings.cache_clear()`` so the entrypoint re-reads ``os.environ``
    after monkeypatching the env vars.
    """
    redis_client = fakeredis.FakeRedis(decode_responses=False)
    storage = InMemoryStorage()

    monkeypatch.setenv("REDIS_URL", "redis://localhost")
    monkeypatch.setenv("MODEL_STORAGE_URL", "http://localhost")

    # Override the Redis and storage factories so real network I/O does not happen.
    monkeypatch.setattr(runner.redis, "from_url", lambda *args, **kwargs: redis_client)
    monkeypatch.setattr("common.storage.factory.IPFSBackend", lambda node_url: storage)

    # make the entrypoint pick up the monkeypatched env
    _common_config.get_settings.cache_clear()

    return redis_client, storage


@pytest.mark.asyncio
async def test_runner_wires_env_and_registers_model(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    redis_client, storage = _setup_runner_env(monkeypatch)
    monkeypatch.setenv("FL_TASK_ID", FL_TASK_ID)
    monkeypatch.setenv("FL_NUM_CLIENTS", str(FL_NUM_CLIENTS))
    monkeypatch.setenv("FL_NUM_ROUNDS", str(FL_NUM_ROUNDS))
    monkeypatch.setenv("FL_MODEL_ID", FL_MODEL_ID)

    await runner.main()

    entry = await ModelRegistry(redis_client, storage).get(FL_MODEL_ID)
    assert entry is not None
    assert "FL run complete" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_runner_honours_configured_strategy_and_persists_provenance(monkeypatch: pytest.MonkeyPatch) -> None:
    redis_client, _storage = _setup_runner_env(monkeypatch)
    monkeypatch.setenv("FL_TASK_ID", "test_runner_strategy_task_id")
    monkeypatch.setenv("FL_NUM_CLIENTS", str(FL_NUM_CLIENTS))
    monkeypatch.setenv("FL_NUM_ROUNDS", str(FL_NUM_ROUNDS))
    monkeypatch.setenv("FL_AGGREGATION_STRATEGY", "fedmedian")
    monkeypatch.setenv("FL_MODEL_ID", "test_runner_strategy_model_id")

    await runner.main()

    records = await cast(
        Awaitable[list[bytes]],
        redis_client.lrange("fl:provenance:test_runner_strategy_task_id", 0, -1),
    )
    assert len(records) == FL_NUM_ROUNDS
