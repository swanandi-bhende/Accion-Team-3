"""Federated learning end to end: the orchestrator, the runner, the network entrypoints, the checkpoint, and real Flower processes."""

from __future__ import annotations

import hashlib
import json
import os
import socket
import subprocess
import sys
import time
import uuid
from collections.abc import Awaitable, Callable, Iterable, Sequence
from pathlib import Path
from typing import Any, cast

import fakeredis
import fakeredis.aioredis as fakeredis_async
import numpy as np
import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from flwr.client import NumPyClient
from flwr.common import Context, NDArrays, Scalar
from flwr.server import start_server
from flwr.server.history import History
from flwr.server.server_config import ServerConfig
from redis.asyncio import Redis

import common.config as _common_config
import federated_learning.runner as runner
import federated_learning.server_app as server_app
from common.child_process import child_env
from common.exceptions import IntegrityCheckFailedError
from common.interfaces import StorageBackend
from common.model_registry import ModelRegistry
from common.models import FLTaskConfig, InferenceRequest, RoundProvenance
from common.onnx_models import linear_model
from common.tensors import decode_tensor, encode_tensor
from federated_learning.orchestrator import (
    FederatedLearningOrchestrator,
    NoClientsRespondedError,
)
from federated_learning.simulated_client import make_simulated_clients
from federated_learning.strategies import ProvenanceStrategy, build_strategy
from inference.engine import InferenceEngine
from inference.tee_runtime import TEERuntime

ROOT = Path(__file__).resolve().parents[2]

PYTHON = sys.executable

HARNESS = ROOT / "scripts" / "fl" / "fl_crash_harness.py"

REDIS_URL = os.environ.get("FL_REDIS_URL", "redis://localhost:6379")

SEALED_KEY = b"mock_sealed_key_0123456789abcdef"


class InMemoryStorage(StorageBackend):
    def __init__(self) -> None:
        self.store: dict[str, bytes] = {}

    async def upload(self, data: bytes) -> str:
        cid = "Qm" + hashlib.sha256(data).hexdigest()
        self.store[cid] = data
        return cid

    async def retrieve(self, cid: str) -> bytes:
        return self.store[cid]


@pytest.mark.asyncio
async def test_property_11_trained_model_runs_through_the_tee_runtime() -> None:
    """Step 15.5: train -> store -> resolve -> execute on the FL-produced model."""
    registry = ModelRegistry(
        fakeredis_async.FakeRedis(decode_responses=False), InMemoryStorage()
    )
    orchestrator = FederatedLearningOrchestrator(
        registry,
        registry.redis,
        make_simulated_clients(3, seed=5),
        model_id="fl-tee",
        architecture="mlp",
    )
    result = await orchestrator.run_training(
        FLTaskConfig(task_id="p11-tee", num_clients=3, num_rounds=3)
    )

    # The registry resolves the trained model's CID back to the exact binary.
    entry = await registry.get("fl-tee")
    assert entry is not None
    assert entry.cid == result.model_cid
    model_binary = await registry.storage.retrieve(entry.cid)
    assert hashlib.sha256(model_binary).hexdigest() == entry.hash == result.model_hash

    # The orchestrator stores the trained weights, not an ONNX model, so the TEE runs an
    # ONNX export of those same parameters. Exporting inside the pipeline is a later step.
    weight, bias = result.final_parameters
    onnx_bytes = linear_model(weight, bias)
    x = np.array([[0.5, -1.0, 2.0, 0.25]], dtype=np.float32)
    tee = TEERuntime("sgx")
    tee.initialize()
    try:
        output = await tee.execute_inference(
            model_binary=onnx_bytes,
            model_hash=hashlib.sha256(onnx_bytes).hexdigest(),
            input_data=encode_tensor(x),
            sealed_key=SEALED_KEY,
        )
    finally:
        tee.shutdown()
    expected = x @ weight.astype(np.float32) + bias.astype(np.float32)
    assert np.allclose(decode_tensor(output), expected, atol=1e-5)

    # A blob whose plaintext does not hash to the expected model hash is refused.
    tee = TEERuntime("sgx")
    tee.initialize()
    try:
        nonce = os.urandom(12)
        sealed_blob = nonce + AESGCM(SEALED_KEY).encrypt(
            nonce, b"some-other-model", None
        )
        with pytest.raises(IntegrityCheckFailedError):
            await tee.execute_inference(
                model_binary=sealed_blob,
                model_hash=hashlib.sha256(b"expected-model").hexdigest(),
                input_data=b"x",
                sealed_key=SEALED_KEY,
            )
    finally:
        tee.shutdown()


def _redis_or_skip() -> Any:
    try:
        import redis as sync_redis
    except ImportError:  # pragma: no cover - redis is a runtime dependency
        pytest.skip("redis package is not installed")
    client = sync_redis.Redis.from_url(REDIS_URL, decode_responses=True)
    try:
        client.ping()
    except Exception:
        pytest.skip("Redis service is not available")
    return client


def test_provenance_survives_a_real_mid_run_process_kill() -> None:
    """Step 15.7: rounds that completed before a crash stay durably in Redis."""
    client = _redis_or_skip()
    task_id = f"crash-{uuid.uuid4().hex[:8]}"
    model_id = f"crash-model-{uuid.uuid4().hex[:8]}"
    key = f"fl:provenance:{task_id}"

    env = child_env(
        REDIS_URL=REDIS_URL,
        FL_TASK_ID=task_id,
        FL_MODEL_ID=model_id,
        FL_NUM_CLIENTS="3",
        FL_NUM_ROUNDS="5",
        FL_ROUND_DELAY="0.4",
    )
    process = subprocess.Popen([PYTHON, str(HARNESS)], cwd=ROOT, env=env)
    try:
        deadline = time.time() + 30
        records: list[str] = []
        while time.time() < deadline:
            records = client.lrange(key, 0, -1)
            if len(records) >= 2:
                break
            time.sleep(0.05)
        assert len(records) >= 2, "harness never persisted any provenance"

        # Crash the run hard, in the middle of its rounds.
        process.kill()
        process.wait(timeout=10)
        assert process.returncode != 0, "the harness was supposed to die mid-run"

        survived = [json.loads(entry) for entry in client.lrange(key, 0, -1)]
        assert len(survived) >= 2, "provenance did not survive the crash"
        assert [record["round_number"] for record in survived] == list(
            range(1, len(survived) + 1)
        )
        assert all(record["participating_clients"] == 3 for record in survived)
        # The run never reached its final round, so no model was ever registered.
        assert client.get(f"model:{model_id}:latest") is None
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)
        client.delete(key)
        client.close()


class _Context:
    def __init__(
        self,
        run_config: dict[str, Any] | None = None,
        node_config: dict[str, Any] | None = None,
    ) -> None:
        self.run_config = run_config or {}
        self.node_config = node_config or {}


def test_client_app_builds_a_flower_client() -> None:
    from federated_learning.client_app import client_fn

    client = client_fn(
        cast(Context, _Context(node_config={"partition-id": 1, "num-partitions": 3}))
    )

    assert client is not None


def test_server_app_builds_components_with_provenance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379")

    components = server_app.server_fn(
        cast(
            Context,
            _Context(
                run_config={
                    "task-id": "net",
                    "num-clients": 3,
                    "num-rounds": 2,
                    "strategy": "fedtrimmedavg",
                }
            ),
        )
    )

    assert isinstance(components.strategy, ProvenanceStrategy)
    assert components.strategy.strategy_name == "fedtrimmedavg"
    assert components.config is not None
    assert components.config.num_rounds == 2


def test_server_app_rejects_unknown_strategy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379")

    with pytest.raises(ValueError, match="Unknown aggregation strategy"):
        server_app.server_fn(
            cast(Context, _Context(run_config={"strategy": "not-a-strategy"}))
        )


def test_redis_recorder_persists_provenance(monkeypatch: pytest.MonkeyPatch) -> None:
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

    stored = cast(list[bytes], fake.lrange("fl:provenance:recorder-task", 0, -1))
    assert len(stored) == 1


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
            raise RuntimeError(
                f"{self.client_id} dropped out in round {config['round']}"
            )
        return super().fit(parameters, config)


def build(
    clients: Sequence[NumPyClient], model_id: str = "fl-model"
) -> tuple[FederatedLearningOrchestrator, ModelRegistry, MockStorageBackend, Any]:
    redis_client = fakeredis_async.FakeRedis(decode_responses=False)
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

    persisted = [
        json.loads(entry)
        for entry in await redis_client.lrange("fl:provenance:crash", 0, -1)
    ]
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

    # The orchestrator stores weights, so the trained parameters are exported to ONNX
    # and registered as a runnable model before the engine can serve them.
    weight, bias = result.final_parameters
    export_cid = await registry.upload_model(
        "fl-e2e-onnx", linear_model(weight, bias), architecture="linear"
    )
    x = np.ones((1, 4), dtype=np.float32)

    tee_runtime = TEERuntime("sgx")
    tee_runtime.initialize()
    try:
        engine = InferenceEngine(
            registry=registry, tee=tee_runtime, redis_client=redis_client
        )
        receipt = await engine.execute(
            InferenceRequest(model_id="fl-e2e-onnx", input_data=encode_tensor(x))
        )

        assert receipt.model_id == "fl-e2e-onnx"
        assert receipt.model_cid == export_cid
        assert receipt.output_hash == hashlib.sha256(receipt.output).hexdigest()
        expected = x @ weight.astype(np.float32) + bias.astype(np.float32)
        assert np.allclose(decode_tensor(receipt.output), expected, atol=1e-5)
    finally:
        tee_runtime.shutdown()


NUM_CLIENTS = 3


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _spawn_clients(address: str, count: int) -> list[subprocess.Popen]:
    """Start ``count`` real Flower client processes via the documented entrypoint."""
    processes = []
    for partition_id in range(count):
        processes.append(
            subprocess.Popen(
                [
                    PYTHON,
                    "-m",
                    "federated_learning.network_runner",
                    "client",
                    "--address",
                    address,
                    "--partition-id",
                    str(partition_id),
                    "--num-partitions",
                    str(count),
                ],
                cwd=ROOT,
                env=child_env(),
            )
        )
    return processes


def _terminate(processes: list[subprocess.Popen]) -> None:
    for process in processes:
        if process.poll() is None:
            process.kill()
    for process in processes:
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:  # pragma: no cover - defensive
            process.kill()


def _run_server(
    config: FLTaskConfig, address: str, on_record: Callable[[RoundProvenance], None]
) -> History:
    strategy = ProvenanceStrategy(
        build_strategy(config), config.aggregation_strategy, on_record=on_record
    )
    return start_server(
        server_address=address,
        strategy=strategy,
        config=ServerConfig(
            num_rounds=config.num_rounds, round_timeout=config.round_timeout
        ),
    )


@pytest.mark.fl_network
def test_real_fl_training_loss_decreases_over_rounds() -> None:
    """Step 15.3: real server + 3 real client processes, loss falls each round."""
    address = f"127.0.0.1:{_free_port()}"
    processes = _spawn_clients(address, NUM_CLIENTS)
    records: list[RoundProvenance] = []
    started = time.time()
    try:
        # Round 1 waits for all three clients. A round timeout well above client start-up time
        # keeps a loaded machine from cutting a round short.
        config = FLTaskConfig(
            task_id="real-loss",
            num_clients=NUM_CLIENTS,
            num_rounds=4,
            round_timeout=60.0,
            min_available_clients=NUM_CLIENTS,
        )
        history = _run_server(config, address, records.append)
    finally:
        _terminate(processes)
    elapsed = time.time() - started

    # Every round ran, over the real transport, with the whole roster participating.
    assert [record.round_number for record in records] == [1, 2, 3, 4]
    assert all(record.participating_clients == NUM_CLIENTS for record in records)

    losses = [loss for _, loss in history.losses_distributed]
    assert len(losses) >= 2, "distributed evaluation produced no per-round loss"
    assert losses[-1] < losses[0], f"training loss did not decrease: {losses}"
    # A working loop should make real progress, not just nudges.
    assert losses[-1] < 0.5 * losses[0], f"loss barely moved: {losses}"
    # Step 15.6: keep real wall-clock numbers visible in the test output.
    print(f"\nreal FL run: {len(losses)} rounds, {elapsed:.2f}s wall clock")


@pytest.mark.fl_network
def test_real_fl_round_completes_after_a_client_disconnects() -> None:
    """Step 15.4: a real mid-training client death does not crash the run.

    The disconnect is triggered from the provenance callback -- i.e. exactly when
    round 1 has been aggregated -- so it is deterministic rather than a timing
    race against round duration.
    """
    address = f"127.0.0.1:{_free_port()}"
    processes = _spawn_clients(address, NUM_CLIENTS)
    records: list[RoundProvenance] = []

    def on_record(record: RoundProvenance) -> None:
        records.append(record)
        if record.round_number == 1:
            processes[1].kill()  # a real process death, not a mocked "K responded" flag

    try:
        config = FLTaskConfig(
            task_id="real-dropout",
            num_clients=NUM_CLIENTS,
            num_rounds=4,
            # Long enough that a slow client start is not mistaken for a dropout.
            round_timeout=60.0,
            # Tolerate one dropout; without this the client manager blocks forever
            # waiting for the missing participant (Requirement 4.5).
            min_available_clients=NUM_CLIENTS - 1,
        )
        history = _run_server(config, address, on_record)
    finally:
        _terminate(processes)

    # run_training-equivalent completed: no exception propagated to the caller.
    assert history is not None
    assert [record.round_number for record in records] == [1, 2, 3, 4]

    participation = [record.participating_clients for record in records]
    # Round 1 starts as soon as the quorum (min_available_clients) has registered, so a
    # late third client can be missing from it. The quorum is the guarantee under test.
    assert participation[0] >= NUM_CLIENTS - 1, f"round 1 below quorum: {participation}"
    assert min(participation) == NUM_CLIENTS - 1, (
        f"reduced count not recorded: {participation}"
    )
    assert participation[-1] == NUM_CLIENTS - 1, (
        "later rounds should run with survivors"
    )
    # The lost client caused a real aggregation failure to be surfaced, not a crash.
    assert processes[1].returncode is not None


FL_TASK_ID = "test_runner_task_id"

FL_MODEL_ID = "test_runner_model_id"

FL_NUM_CLIENTS = 2

FL_NUM_ROUNDS = 2


def _setup_runner_env(monkeypatch: pytest.MonkeyPatch) -> tuple[Redis, InMemoryStorage]:
    """Set env vars and factory overrides that runner.main() needs.

    We call ``get_settings.cache_clear()`` so the entrypoint re-reads ``os.environ``
    after monkeypatching the env vars.
    """
    redis_client = fakeredis_async.FakeRedis(decode_responses=False)
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
async def test_runner_wires_env_and_registers_model(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
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
async def test_runner_honours_configured_strategy_and_persists_provenance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
