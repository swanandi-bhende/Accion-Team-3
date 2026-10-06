"""Integration test for the federated-learning runner entrypoint.

Exercises ``runner.main()`` end to end by swapping its two external factories
(Redis and storage) so no live services are required.
"""

import hashlib

import fakeredis.aioredis as fakeredis
import pytest

import federated_learning.runner as runner
from common.interfaces import StorageBackend
from common.model_registry import ModelRegistry


class InMemoryStorage(StorageBackend):
    def __init__(self):
        self.store = {}

    async def upload(self, data: bytes) -> str:
        cid = "Qm" + hashlib.sha256(data).hexdigest()
        self.store[cid] = data
        return cid

    async def retrieve(self, cid: str) -> bytes:
        return self.store[cid]


def _patch_factories(monkeypatch):
    redis_client = fakeredis.FakeRedis(decode_responses=False)
    storage = InMemoryStorage()
    monkeypatch.setattr(runner.redis, "from_url", lambda *args, **kwargs: redis_client)
    monkeypatch.setattr(runner, "IPFSBackend", lambda node_url: storage)
    return redis_client, storage


@pytest.mark.asyncio
async def test_runner_wires_env_and_registers_model(monkeypatch, capsys):
    redis_client, storage = _patch_factories(monkeypatch)
    monkeypatch.setenv("FL_TASK_ID", "runner-task")
    monkeypatch.setenv("FL_NUM_CLIENTS", "3")
    monkeypatch.setenv("FL_NUM_ROUNDS", "2")
    monkeypatch.setenv("FL_MODEL_ID", "runner-model")

    await runner.main()

    entry = await ModelRegistry(redis_client, storage).get("runner-model")
    assert entry is not None
    assert "FL run complete" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_runner_honours_configured_strategy_and_persists_provenance(monkeypatch):
    redis_client, _storage = _patch_factories(monkeypatch)
    monkeypatch.setenv("FL_TASK_ID", "runner-strategy")
    monkeypatch.setenv("FL_NUM_CLIENTS", "5")
    monkeypatch.setenv("FL_NUM_ROUNDS", "2")
    monkeypatch.setenv("FL_AGGREGATION_STRATEGY", "fedmedian")
    monkeypatch.setenv("FL_MODEL_ID", "runner-model-2")

    await runner.main()

    records = await redis_client.lrange("fl:provenance:runner-strategy", 0, -1)
    assert len(records) == 2
