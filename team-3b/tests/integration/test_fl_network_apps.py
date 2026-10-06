"""Tests for the network (SuperLink) federated-learning entrypoints.

These validate the wiring of the server/client apps and the provenance recorder.
The SuperLink runtime itself is a deployment concern documented in
docs/FEDERATED_LEARNING.md and cannot be exercised in-process.
"""

import fakeredis
import pytest

import federated_learning.server_app as server_app
from common.models import RoundProvenance
from federated_learning.strategies import ProvenanceStrategy


class _Context:
    def __init__(self, run_config=None, node_config=None):
        self.run_config = run_config or {}
        self.node_config = node_config or {}


def test_client_app_builds_a_flower_client():
    from federated_learning.client_app import client_fn

    client = client_fn(_Context(node_config={"partition-id": 1, "num-partitions": 3}))

    assert client is not None


def test_server_app_builds_components_with_provenance(monkeypatch):
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379")

    components = server_app.server_fn(
        _Context(run_config={"task-id": "net", "num-clients": 3, "num-rounds": 2, "strategy": "fedtrimmedavg"})
    )

    assert isinstance(components.strategy, ProvenanceStrategy)
    assert components.strategy.strategy_name == "fedtrimmedavg"
    assert components.config.num_rounds == 2


def test_server_app_rejects_unknown_strategy(monkeypatch):
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379")

    with pytest.raises(ValueError, match="Unknown aggregation strategy"):
        server_app.server_fn(_Context(run_config={"strategy": "not-a-strategy"}))


def test_redis_recorder_persists_provenance(monkeypatch):
    fake = fakeredis.FakeRedis()
    monkeypatch.setattr(server_app.sync_redis.Redis, "from_url", lambda url: fake)

    record = server_app.build_redis_recorder("recorder-task", "redis://localhost:6379")
    record(
        RoundProvenance(
            round_number=1,
            participating_clients=2,
            aggregation_strategy="fedavg",
            round_duration=0.1,
            resulting_model_hash="deadbeef",
        )
    )

    stored = fake.lrange("fl:provenance:recorder-task", 0, -1)
    assert len(stored) == 1
