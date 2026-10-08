"""Federated Learning orchestrator built on Flower.

Runs FedAvg rounds over registered Flower clients, records per-round provenance
durably in Redis as each round completes, and stores the final aggregated model
through the Model Registry so the Inference Engine can load it.

The design keeps three concerns separable and testable:

* aggregation is delegated to a Flower ``Strategy`` (FedAvg by default), with an
  independent reference implementation of the same arithmetic in
  :func:`weighted_average`;
* distribution, timeouts, and malformed-update rejection live in
  ``_execute_round``;
* provenance persistence and model storage live in ``run_training``.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass

import numpy as np
from flwr.common import (
    Code,
    FitRes,
    Status,
    ndarrays_to_parameters,
    parameters_to_ndarrays,
)
from flwr.server.strategy import Strategy

from common.model_registry import ModelRegistry
from common.models import FLTaskConfig, RoundProvenance
from federated_learning.strategies import STRATEGY_BUILDERS as _STRATEGY_BUILDERS
from federated_learning.weights import (
    parameters_hash,
    serialize_parameters,
)

logger = logging.getLogger(__name__)


class NoClientsRespondedError(RuntimeError):
    """Raised when a round receives zero usable client updates.

    ``min_fit_clients=1`` means "at least one client", so zero responses leaves
    the global model undefined. Rather than silently advancing a stale model we
    fail the run loudly; callers can retry the task.
    """


@dataclass
class TrainingResult:
    model_cid: str
    model_hash: str
    final_parameters: list[np.ndarray]
    provenance: list[RoundProvenance]


class LocalFlowerServer:
    """In-process stand-in for a Flower server's registered client roster.

    Holds the current global parameters and the registered ``NumPyClient``
    instances. A production runner swaps this for a network ``start_server``;
    the round loop is unchanged because it only relies on Flower's ``NumPyClient``
    and ``Strategy`` interfaces.
    """

    def __init__(self, clients, initial_parameters, round_number: int = 0):
        self.clients = list(clients)
        self.parameters = [np.asarray(parameter, dtype=np.float64) for parameter in initial_parameters]
        self.round_number = round_number

    @property
    def client_ids(self) -> list[str]:
        return [getattr(client, "client_id", f"client-{index}") for index, client in enumerate(self.clients)]


class FederatedLearningOrchestrator:
    """Drive Flower federated-learning rounds and persist the resulting model."""

    STRATEGY_BUILDERS = _STRATEGY_BUILDERS
    PROVENANCE_KEY_PREFIX = "fl:provenance:"

    def __init__(
        self,
        registry: ModelRegistry,
        redis_client,
        clients,
        model_id: str,
        architecture: str | None = None,
        clock: Callable[[], float] = time.time,
    ):
        if not clients:
            raise ValueError("FederatedLearningOrchestrator requires at least one client")
        self.registry = registry
        self.redis = redis_client
        self.clients = list(clients)
        self.model_id = model_id
        self.architecture = architecture
        self.clock = clock

    def _build_strategy(self, config: FLTaskConfig) -> Strategy:
        """Select the aggregation strategy, failing loudly on unknown names."""
        name = (config.aggregation_strategy or "").lower()
        builder = self.STRATEGY_BUILDERS.get(name)
        if builder is None:
            raise ValueError(
                f"Unknown aggregation strategy: {config.aggregation_strategy!r}. "
                f"Supported: {sorted(self.STRATEGY_BUILDERS)}"
            )
        return builder(config)

    def _initial_parameters(self) -> list[np.ndarray]:
        return [np.asarray(parameter, dtype=np.float64) for parameter in self.clients[0].get_parameters({})]

    async def _execute_round(self, server: LocalFlowerServer, round_timeout: float):
        """Distribute the global model and collect fit results within the timeout."""
        server.round_number += 1
        global_parameters = server.parameters
        expected_shapes = [parameter.shape for parameter in global_parameters]

        results: list[tuple[str, FitRes]] = []
        failures: list[tuple[str, BaseException]] = []
        timed_out: list[str] = []

        for client_id, client in zip(server.client_ids, server.clients, strict=True):
            try:
                parameters, num_examples, metrics = await asyncio.wait_for(
                    asyncio.to_thread(client.fit, list(global_parameters), {"round": server.round_number}),
                    timeout=round_timeout,
                )
            except TimeoutError:
                timed_out.append(client_id)
                failures.append((client_id, TimeoutError(f"{client_id} exceeded {round_timeout}s")))
                continue
            except Exception as error:  # a client-side failure must not abort the round
                failures.append((client_id, error))
                continue

            if not self._valid_update(parameters, expected_shapes):
                logger.warning("Discarding malformed update from client_id=%s", client_id)
                failures.append((client_id, ValueError(f"{client_id} returned a malformed update")))
                continue

            results.append(
                (
                    client_id,
                    FitRes(
                        status=Status(Code.OK, ""),
                        parameters=ndarrays_to_parameters(parameters),
                        num_examples=num_examples,
                        metrics=metrics,
                    ),
                )
            )

        if timed_out:
            logger.warning(
                "FL round %s timed out waiting for clients: %s", server.round_number, sorted(timed_out)
            )
        return results, failures, timed_out

    @staticmethod
    def _valid_update(parameters, expected_shapes: Sequence[tuple[int, ...]]) -> bool:
        try:
            arrays = [np.asarray(parameter, dtype=np.float64) for parameter in parameters]
        except (TypeError, ValueError):
            return False
        if len(arrays) != len(expected_shapes):
            return False
        return all(array.shape == shape for array, shape in zip(arrays, expected_shapes, strict=True))

    async def _persist_provenance(self, task_id: str, record: RoundProvenance) -> None:
        await self.redis.rpush(f"{self.PROVENANCE_KEY_PREFIX}{task_id}", record.model_dump_json())

    async def run_training(self, config: FLTaskConfig) -> TrainingResult:
        """Run every configured round and store the final model."""
        strategy = self._build_strategy(config)
        server = LocalFlowerServer(self.clients, self._initial_parameters())
        provenance: list[RoundProvenance] = []

        for _ in range(config.num_rounds):
            round_started = self.clock()
            results, failures, _timed_out = await self._execute_round(server, config.round_timeout)
            if not results:
                raise NoClientsRespondedError(
                    f"No clients responded in round {server.round_number} for task {config.task_id}"
                )

            aggregated, _metrics = strategy.aggregate_fit(server.round_number, results, failures)
            if aggregated is None:
                raise NoClientsRespondedError(
                    f"Strategy produced no parameters in round {server.round_number}"
                )
            server.parameters = [np.asarray(p, dtype=np.float64) for p in parameters_to_ndarrays(aggregated)]

            record = RoundProvenance(
                round_number=server.round_number,
                participating_clients=len(results),
                aggregation_strategy=config.aggregation_strategy,
                round_duration=self.clock() - round_started,
                resulting_model_hash=parameters_hash(server.parameters),
            )
            provenance.append(record)
            await self._persist_provenance(config.task_id, record)

        model_binary = serialize_parameters(server.parameters)
        model_hash = hashlib.sha256(model_binary).hexdigest()
        model_cid = await self.registry.upload_model(
            self.model_id, model_binary, architecture=self.architecture
        )
        return TrainingResult(
            model_cid=model_cid,
            model_hash=model_hash,
            final_parameters=server.parameters,
            provenance=provenance,
        )
