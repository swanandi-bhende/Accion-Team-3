"""Property tests for the Federated Learning Orchestrator.

Property 9: aggregated weights equal the elementwise (weighted) mean of client
    weights, verified against our own reference implementation and Flower's FedAvg.
Property 10: when only K of N clients respond, aggregation proceeds with exactly
    those K and provenance records the real participation count.
Property 11: a completed training job stores a model whose CID resolves back to
    the exact trained binary.
"""

import hashlib
import logging
import time
from collections.abc import Callable, Sequence
from typing import Any

import fakeredis.aioredis as fakeredis
import numpy as np
import pytest
from flwr.client import NumPyClient
from flwr.common import (
    Code,
    FitRes,
    NDArrays,
    Parameters,
    Scalar,
    Status,
    ndarrays_to_parameters,
    parameters_to_ndarrays,
)
from flwr.server.client_proxy import ClientProxy
from flwr.server.strategy import FedAvg
from hypothesis import given, settings
from hypothesis import strategies as st

from common.interfaces import StorageBackend
from common.model_registry import ModelRegistry
from common.models import FLTaskConfig, RoundProvenance
from federated_learning.orchestrator import (
    FederatedLearningOrchestrator,
    NoClientsRespondedError,
)
from federated_learning.simulated_client import make_simulated_clients
from federated_learning.strategies import (
    STRATEGY_BUILDERS,
    ProvenanceStrategy,
    build_strategy,
)
from federated_learning.weights import (
    deserialize_parameters,
    serialize_parameters,
    weighted_average,
)

MODEL_ID = "fl-model"


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
    """A well-behaved client that always returns the same weights."""

    def __init__(self, client_id: str, weights: NDArrays) -> None:
        self.client_id = client_id
        self._weights = [np.asarray(weight, dtype=np.float64) for weight in weights]

    def get_parameters(self, config: dict[str, Scalar]) -> NDArrays:
        return [weight.copy() for weight in self._weights]

    def fit(self, parameters: NDArrays, config: dict[str, Scalar]) -> tuple[NDArrays, int, dict[str, Scalar]]:
        return [weight.copy() for weight in self._weights], 1, {}

    def evaluate(self, parameters: NDArrays, config: dict[str, Scalar]) -> tuple[float, int, dict[str, Scalar]]:
        return 0.0, 1, {}


class FailingClient(FixedClient):
    def fit(self, parameters: NDArrays, config: dict[str, Scalar]) -> tuple[NDArrays, int, dict[str, Scalar]]:
        raise RuntimeError(f"{self.client_id} failed")


class SlowClient(FixedClient):
    def __init__(self, client_id: str, weights: NDArrays, delay: float) -> None:
        super().__init__(client_id, weights)
        self.delay = delay

    def fit(self, parameters: NDArrays, config: dict[str, Scalar]) -> tuple[NDArrays, int, dict[str, Scalar]]:
        time.sleep(self.delay)
        return super().fit(parameters, config)


class MalformedClient(FixedClient):
    def fit(self, parameters: NDArrays, config: dict[str, Scalar]) -> tuple[NDArrays, int, dict[str, Scalar]]:
        return [np.zeros((1,))], 1, {}


def build_orchestrator(
    clients: Sequence[NumPyClient], clock: Callable[[], float] = time.time
) -> tuple[FederatedLearningOrchestrator, ModelRegistry, MockStorageBackend, Any]:
    redis_client = fakeredis.FakeRedis(decode_responses=False)
    storage = MockStorageBackend()
    registry = ModelRegistry(redis_client, storage)
    orchestrator = FederatedLearningOrchestrator(
        registry, redis_client, clients, model_id=MODEL_ID, architecture="mlp", clock=clock
    )
    return orchestrator, registry, storage, redis_client


# --------------------------------------------------------------------------- #
# Property 9 - aggregation equals the reference mean (14.7)
# --------------------------------------------------------------------------- #

_WEIGHT_FLOATS = st.floats(
    min_value=-1e3, max_value=1e3, allow_nan=False, allow_infinity=False, width=32
)


@settings(max_examples=50, deadline=None)
@given(
    num_clients=st.integers(min_value=1, max_value=6),
    dimension=st.integers(min_value=1, max_value=6),
    values=st.lists(_WEIGHT_FLOATS, min_size=36, max_size=36),
)
def test_property_9_weighted_average_is_mean_for_equal_sizes(num_clients: int, dimension: int, values: list[float]) -> None:
    vectors = [
        np.array(values[index * dimension : (index + 1) * dimension], dtype=np.float64).reshape(-1, 1)  # type: ignore[attr-defined]
        for index in range(num_clients)
    ]

    aggregated = weighted_average([[vector] for vector in vectors])[0]
    expected = np.mean(vectors, axis=0)

    assert np.allclose(aggregated, expected, atol=1e-9)


def test_property_9_fedavg_aggregation_matches_reference() -> None:
    strategy = FedAvg(min_fit_clients=1, min_evaluate_clients=1, min_available_clients=1)
    updates = [
        [np.array([[1.0], [2.0]]), np.array([0.5])],
        [np.array([[3.0], [4.0]]), np.array([1.5])],
        [np.array([[5.0], [6.0]]), np.array([2.5])],
    ]
    results = [
        (
            f"client-{index}",
            FitRes(
                status=Status(Code.OK, ""),
                parameters=ndarrays_to_parameters(update),
                num_examples=10,
                metrics={},
            ),
        )
        for index, update in enumerate(updates)
    ]

    aggregated, _ = strategy.aggregate_fit(1, results, [])  # type: ignore[arg-type]
    assert aggregated is not None
    flower_result = parameters_to_ndarrays(aggregated)
    reference = weighted_average(updates, weights=[10, 10, 10])

    for got, expected in zip(flower_result, reference, strict=True):
        assert np.allclose(got, expected, atol=1e-9)


# --------------------------------------------------------------------------- #
# Property 10 - K of N responders (14.4 / 14.8)
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
@pytest.mark.parametrize("total_clients, responding", [(3, 1), (3, 2), (3, 3), (5, 1), (5, 2), (5, 4)])
async def test_property_10_aggregation_proceeds_with_responding_subset(total_clients: int, responding: int) -> None:
    base_weights = [np.array([[1.0], [2.0]]), np.array([0.5])]
    clients = [FixedClient(f"client-{index}", base_weights) for index in range(responding)]
    clients += [FailingClient(f"client-{index}", base_weights) for index in range(responding, total_clients)]
    orchestrator, _, _, _ = build_orchestrator(clients)

    config = FLTaskConfig(task_id="p10", num_clients=total_clients, num_rounds=1)
    result = await orchestrator.run_training(config)

    assert result.provenance[0].participating_clients == responding
    assert result.provenance[0].aggregation_strategy == "fedavg"

    aggregated = deserialize_parameters(serialize_parameters(result.final_parameters))
    for got, expected in zip(aggregated, base_weights, strict=True):
        assert np.allclose(got, np.asarray(expected), atol=1e-9)


@pytest.mark.asyncio
async def test_property_10_timed_out_clients_excluded_and_logged(caplog: pytest.LogCaptureFixture) -> None:
    base_weights = [np.array([[1.0], [2.0]]), np.array([0.5])]
    clients = [
        FixedClient("fast", base_weights),
        SlowClient("slow", base_weights, delay=0.2),
    ]
    orchestrator, _, _, _ = build_orchestrator(clients)
    config = FLTaskConfig(task_id="p10-timeout", num_clients=2, num_rounds=1, round_timeout=0.01)

    with caplog.at_level(logging.WARNING):
        result = await orchestrator.run_training(config)

    assert result.provenance[0].participating_clients == 1
    assert "slow" in caplog.text


@pytest.mark.asyncio
async def test_a_round_is_bounded_by_one_timeout_not_one_per_client() -> None:
    """Requirement 4.5: the round timeout bounds the whole round, not each client in turn."""
    base_weights = [np.array([[1.0], [2.0]]), np.array([0.5])]
    clients = [FixedClient("fast", base_weights)] + [SlowClient(f"slow-{i}", base_weights, delay=1.0) for i in range(2)]
    orchestrator, _, _, _ = build_orchestrator(clients)
    config = FLTaskConfig(task_id="r4-bound", num_clients=3, num_rounds=1, round_timeout=0.3)

    result = await orchestrator.run_training(config)

    assert result.provenance[0].participating_clients == 1
    assert result.provenance[0].round_duration < 0.5, f"round took {result.provenance[0].round_duration:.2f} s"


# --------------------------------------------------------------------------- #
# Property 11 - trained model stored and resolvable (14.5 / 14.6)
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_property_11_trained_model_is_stored_and_resolvable() -> None:
    clients = make_simulated_clients(3, seed=1)
    orchestrator, registry, storage, redis_client = build_orchestrator(clients)
    config = FLTaskConfig(task_id="p11", num_clients=3, num_rounds=3)

    result = await orchestrator.run_training(config)

    entry = await registry.get(MODEL_ID)
    assert entry is not None
    assert entry.cid == result.model_cid
    assert entry.hash == result.model_hash

    stored_binary = await storage.retrieve(entry.cid)
    assert hashlib.sha256(stored_binary).hexdigest() == entry.hash
    restored = deserialize_parameters(stored_binary)
    for got, expected in zip(restored, result.final_parameters, strict=True):
        assert np.allclose(got, expected)

    persisted = await redis_client.lrange("fl:provenance:p11", 0, -1)
    assert len(persisted) == 3
    assert [record.round_number for record in result.provenance] == [1, 2, 3]
    assert all(record.participating_clients == 3 for record in result.provenance)


# --------------------------------------------------------------------------- #
# Strategy selection (14.3)
# --------------------------------------------------------------------------- #

def test_build_strategy_defaults_to_fedavg_with_the_full_roster_as_quorum() -> None:
    strategy = build_strategy(FLTaskConfig(task_id="s", num_clients=3, num_rounds=1))

    assert isinstance(strategy, FedAvg)
    assert strategy.min_fit_clients == 3
    assert strategy.min_available_clients == 3


def test_build_strategy_rejects_unknown_name_instead_of_silently_defaulting() -> None:
    with pytest.raises(ValueError, match="Unknown aggregation strategy"):
        build_strategy(
            FLTaskConfig(task_id="s", num_clients=2, num_rounds=1, aggregation_strategy="definitely-not-a-strategy")
        )


def test_simulated_client_evaluate_reports_local_loss() -> None:
    client = make_simulated_clients(1, seed=3)[0]
    loss, num_examples, metrics = client.evaluate(client.get_parameters({}), {})

    assert num_examples > 0
    assert np.isfinite(loss)
    assert "mse" in metrics


def test_weighted_average_rejects_invalid_inputs() -> None:
    single = [np.array([[1.0], [2.0]])]

    with pytest.raises(ValueError, match="at least one update"):
        weighted_average([])
    with pytest.raises(ValueError, match="equal length"):
        weighted_average([single], weights=[1, 2])
    with pytest.raises(ValueError, match="positive value"):
        weighted_average([single], weights=[0])
    with pytest.raises(ValueError, match="same parameter shapes"):
        weighted_average([[np.zeros((2, 1))], [np.zeros((3, 1))]])


def test_orchestrator_requires_at_least_one_client() -> None:
    with pytest.raises(ValueError, match="at least one client"):
        FederatedLearningOrchestrator(None, None, [], model_id=MODEL_ID)  # type: ignore[arg-type]


def test_strategy_registry_is_extensible_without_touching_run_training() -> None:
    builders = STRATEGY_BUILDERS
    builders["custom"] = lambda config: FedAvg(min_fit_clients=1, min_available_clients=1)
    try:
        strategy = build_strategy(
            FLTaskConfig(task_id="s", num_clients=2, num_rounds=1, aggregation_strategy="CUSTOM")
        )
        assert isinstance(strategy, FedAvg)
    finally:
        del builders["custom"]


@pytest.mark.parametrize("strategy_name", ["fedavg", "fedmedian", "fedtrimmedavg", "krum", "dp_fedavg"])
def test_every_registered_strategy_builds_and_aggregates(strategy_name: str) -> None:
    config = FLTaskConfig(task_id="s", num_clients=5, num_rounds=1, aggregation_strategy=strategy_name)

    strategy = build_strategy(config)
    updates = [[np.array([[float(index)], [float(index) + 1.0]])] for index in range(5)]
    results = [
        (
            f"client-{index}",
            FitRes(
                status=Status(Code.OK, ""),
                parameters=ndarrays_to_parameters(update),
                num_examples=1,
                metrics={},
            ),
        )
        for index, update in enumerate(updates)
    ]

    aggregated, _ = strategy.aggregate_fit(1, results, [])  # type: ignore[arg-type]

    assert aggregated is not None
    arrays = parameters_to_ndarrays(aggregated)
    assert arrays and np.all(np.isfinite(arrays[0]))


def test_provenance_strategy_records_each_aggregated_round() -> None:
    recorded: list[RoundProvenance] = []
    inner = FedAvg(min_fit_clients=1, min_evaluate_clients=1, min_available_clients=3)
    strategy = ProvenanceStrategy(inner, "fedavg", on_record=recorded.append, clock=lambda: 1.0)
    results = [
        (
            "client-0",
            FitRes(
                status=Status(Code.OK, ""),
                parameters=ndarrays_to_parameters([np.array([[1.0]])]),
                num_examples=1,
                metrics={},
            ),
        )
    ]

    strategy.aggregate_fit(1, results, [])  # type: ignore[arg-type]

    assert len(strategy.records) == 1
    assert strategy.records[0].participating_clients == 1
    assert strategy.records[0].aggregation_strategy == "fedavg"
    assert recorded == strategy.records
    # Unhandled attributes/delegation still reach the wrapped strategy.
    assert strategy.min_fit_clients == inner.min_fit_clients


def test_fl_task_config_rejects_invalid_strategy_parameters() -> None:
    with pytest.raises(ValueError):
        FLTaskConfig(task_id="x", num_clients=1, num_rounds=1, trim_fraction=0.7)
    with pytest.raises(ValueError):
        FLTaskConfig(task_id="x", num_clients=1, num_rounds=1, dp_clip_norm=0)
    with pytest.raises(ValueError):
        FLTaskConfig(task_id="x", num_clients=1, num_rounds=1, num_malicious_clients=-1)


def test_valid_update_rejects_malformed_types_and_shapes() -> None:
    """Guards the defensive branches: a non-numeric or wrong-shaped update is dropped."""
    expected = [(2, 1)]

    assert FederatedLearningOrchestrator._valid_update([["a", "b"]], expected) is False  # ValueError
    assert FederatedLearningOrchestrator._valid_update([None], expected) is False  # TypeError
    assert FederatedLearningOrchestrator._valid_update([], expected) is False  # wrong arity
    assert FederatedLearningOrchestrator._valid_update([np.zeros((3, 1))], expected) is False  # wrong shape
    assert FederatedLearningOrchestrator._valid_update([np.zeros((2, 1))], expected) is True


class _NullAggregatingStrategy(FedAvg):
    """Selects no parameters, emulating a strategy that refuses to aggregate."""

    def aggregate_fit(
        self,
        server_round: int,
        results: list[tuple[ClientProxy, FitRes]],
        failures: list[tuple[ClientProxy, FitRes] | BaseException],
    ) -> tuple[Parameters | None, dict[str, Scalar]]:
        return None, {}


@pytest.mark.asyncio
async def test_run_training_fails_when_strategy_produces_no_parameters() -> None:
    orchestrator, _, _, _ = build_orchestrator(make_simulated_clients(2))
    builders = STRATEGY_BUILDERS
    builders["null-agg"] = lambda config: _NullAggregatingStrategy(
        min_fit_clients=1, min_available_clients=config.num_clients
    )
    try:
        config = FLTaskConfig(task_id="null", num_clients=2, num_rounds=1, aggregation_strategy="null-agg")
        with pytest.raises(NoClientsRespondedError, match="no parameters"):
            await orchestrator.run_training(config)
    finally:
        del builders["null-agg"]


def test_provenance_strategy_getattr_guard_prevents_unpickling_recursion() -> None:
    strategy = ProvenanceStrategy(FedAvg(min_fit_clients=1, min_available_clients=1), "fedavg")

    with pytest.raises(AttributeError, match="_inner"):
        strategy.__getattr__("_inner")
    # A genuinely absent attribute still delegates to the wrapped strategy and errors there.
    with pytest.raises(AttributeError):
        strategy.__getattr__("definitely_not_on_a_strategy")
