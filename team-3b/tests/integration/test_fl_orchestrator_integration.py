"""Integration tests for the Federated Learning Orchestrator.

Covers realistic failure scenarios and the Step 14 <-> Step 3/4/5 integration
point: a model trained by the orchestrator must be immediately retrievable from
the Model Registry and loadable by the Inference Engine / TEE Runtime.
"""

import hashlib
import json
from collections.abc import Iterable, Sequence
from typing import Any

import fakeredis.aioredis as fakeredis
import numpy as np
import pytest
from flwr.client import NumPyClient
from flwr.common import NDArrays, Scalar

from common.interfaces import StorageBackend
from common.model_registry import ModelRegistry
from common.models import FLTaskConfig, InferenceRequest
from federated_learning.orchestrator import (
    FederatedLearningOrchestrator,
    NoClientsRespondedError,
)
from federated_learning.simulated_client import make_simulated_clients
from inference.engine import InferenceEngine
from inference.tee_runtime import TEERuntime

BASELINE_WEIGHTS = [np.array([[1.0], [2.0]]), np.array([0.5])]


class MockStorageBackend(StorageBackend):
    def __init__(self) -> None:
        self.store: dict[str, bytes] = {}

    async def upload(self, data: bytes) -> str:
        cid = "Qm" + hashlib.sha256(data).hexdigest()
        self.store[cid] = data
        return cid

    async def retrieve(self, cid: str) -> bytes:
        if cid not in self.store:
            raise Exception("Not found")
        return self.store[cid]


class FixedClient(NumPyClient):
    def __init__(self, client_id: str, weights: NDArrays = BASELINE_WEIGHTS) -> None:
        self.client_id = client_id
        self._weights = [np.asarray(weight, dtype=np.float64) for weight in weights]

    def get_parameters(self, config: dict[str, Scalar]) -> NDArrays:
        return [weight.copy() for weight in self._weights]

    def fit(
        self, parameters: NDArrays, config: dict[str, Scalar]
    ) -> tuple[NDArrays, int, dict[str, Scalar]]:
        return [weight.copy() for weight in self._weights], 1, {}


class FailingClient(FixedClient):
    def fit(
        self, parameters: NDArrays, config: dict[str, Scalar]
    ) -> tuple[NDArrays, int, dict[str, Scalar]]:
        raise RuntimeError(f"{self.client_id} cannot serve this round")


class MalformedClient(FixedClient):
    def fit(
        self, parameters: NDArrays, config: dict[str, Scalar]
    ) -> tuple[NDArrays, int, dict[str, Scalar]]:
        return [np.zeros((7, 3))], 1, {}


class DropoutClient(FixedClient):
    def __init__(self, client_id: str, inactive_rounds: Iterable[int]) -> None:
        super().__init__(client_id)
        self.inactive_rounds = set(inactive_rounds)

    def fit(
        self, parameters: NDArrays, config: dict[str, Any]
    ) -> tuple[NDArrays, int, dict[str, Scalar]]:
        if config.get("round") in self.inactive_rounds:
            raise RuntimeError(f"{self.client_id} dropped out in round {config['round']}")
        return super().fit(parameters, config)


def build(
    clients: Sequence[NumPyClient], model_id: str = "fl-model"
) -> tuple[FederatedLearningOrchestrator, ModelRegistry, MockStorageBackend, Any]:
    redis_client = fakeredis.FakeRedis(decode_responses=False)
    storage = MockStorageBackend()
    registry = ModelRegistry(redis_client, storage)
    orchestrator = FederatedLearningOrchestrator(
        registry, redis_client, clients, model_id=model_id, architecture="mlp"
    )
    return orchestrator, registry, storage, redis_client


@pytest.mark.asyncio
async def test_zero_responding_clients_is_a_hard_failure() -> None:
    """Decision: zero responses fails the run rather than advancing a stale model."""
    clients = [FailingClient("a"), FailingClient("b")]
    orchestrator, registry, _, redis_client = build(clients)
    config = FLTaskConfig(task_id="zero", num_clients=2, num_rounds=2)

    with pytest.raises(NoClientsRespondedError):
        await orchestrator.run_training(config)

    # Nothing was recorded or stored for a round that never produced a model.
    assert await redis_client.lrange("fl:provenance:zero", 0, -1) == []
    assert await registry.get("fl-model") is None


@pytest.mark.asyncio
async def test_malformed_wrong_shaped_update_is_rejected_without_crashing() -> None:
    clients = [FixedClient("good"), MalformedClient("bad")]
    orchestrator, registry, _, _ = build(clients)
    config = FLTaskConfig(task_id="malformed", num_clients=2, num_rounds=1)

    result = await orchestrator.run_training(config)

    assert result.provenance[0].participating_clients == 1
    assert await registry.get("fl-model") is not None


@pytest.mark.asyncio
async def test_varying_participation_is_reflected_per_round() -> None:
    clients = [
        FixedClient("always"),
        DropoutClient("sometimes", inactive_rounds={2}),
        DropoutClient("seldom", inactive_rounds={2, 3}),
    ]
    orchestrator, _, _, redis_client = build(clients)
    config = FLTaskConfig(task_id="varying", num_clients=3, num_rounds=3)

    result = await orchestrator.run_training(config)

    assert [record.participating_clients for record in result.provenance] == [3, 1, 2]

    persisted = await redis_client.lrange("fl:provenance:varying", 0, -1)
    counts = [json.loads(entry)["participating_clients"] for entry in persisted]
    assert counts == [3, 1, 2]
    assert [json.loads(entry)["round_number"] for entry in persisted] == [1, 2, 3]


@pytest.mark.asyncio
async def test_provenance_for_completed_rounds_survives_a_mid_run_failure() -> None:
    """14.5: rounds that finished before a crash stay durably recorded in Redis.

    Provenance is written incrementally per round (not batched at the end), so a
    run that dies on round 3 of 4 must still leave rounds 1-2 behind for audit.
    """
    clients = [
        DropoutClient("a", inactive_rounds={3}),
        DropoutClient("b", inactive_rounds={3}),
    ]
    orchestrator, registry, _, redis_client = build(clients)
    config = FLTaskConfig(task_id="crash", num_clients=2, num_rounds=4)

    with pytest.raises(NoClientsRespondedError):
        await orchestrator.run_training(config)

    persisted = [json.loads(entry) for entry in await redis_client.lrange("fl:provenance:crash", 0, -1)]
    assert [record["round_number"] for record in persisted] == [1, 2]
    assert all(record["participating_clients"] == 2 for record in persisted)
    # The failed round never produced a model, so nothing is registered yet.
    assert await registry.get("fl-model") is None


@pytest.mark.asyncio
async def test_trained_model_is_loadable_by_the_inference_pipeline() -> None:
    clients = make_simulated_clients(3, seed=2)
    orchestrator, registry, _, redis_client = build(clients, model_id="fl-e2e")
    config = FLTaskConfig(task_id="e2e", num_clients=3, num_rounds=2)

    result = await orchestrator.run_training(config)

    entry = await registry.get("fl-e2e")
    assert entry is not None
    assert entry.cid == result.model_cid
    assert entry.hash == result.model_hash

    tee_runtime = TEERuntime("sgx")
    tee_runtime.initialize()
    try:
        engine = InferenceEngine(registry=registry, tee=tee_runtime, redis_client=redis_client)
        receipt = await engine.execute(InferenceRequest(model_id="fl-e2e", input_data=b"hello fl"))

        assert receipt.model_id == "fl-e2e"
        assert receipt.model_cid == result.model_cid
        assert receipt.output_hash == hashlib.sha256(receipt.output).hexdigest()
    finally:
        tee_runtime.shutdown()
