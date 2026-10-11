#!/usr/bin/env python
"""Deliberately slow federated-training harness for crash-durability testing.

Runs the real ``FederatedLearningOrchestrator.run_training`` path against a
**real Redis** (so provenance written by one process is visible to another) with
a configurable per-round delay, so an external supervisor can kill the process
part-way through and then inspect what survived.

This exists to exercise the Step 14.5 design decision under a genuine failure --
"training crashes on round 7 of 10 and rounds 1-6 are still on record" -- rather
than asserting in code that incremental persistence happens.

Storage is an in-memory backend on purpose: the final model upload only happens
after the last round, which a mid-run crash never reaches, so it is irrelevant to
what is being tested. Everything that matters (the round loop, strategy
aggregation, and per-round Redis writes) is the production code path.

Environment::

    REDIS_URL        redis://localhost:6379   (required)
    FL_TASK_ID       crash-harness-task
    FL_MODEL_ID      crash-harness-model
    FL_NUM_CLIENTS   3
    FL_NUM_ROUNDS    5
    FL_ROUND_DELAY   0.5   seconds of work per client per round
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import time
from typing import Any

import redis.asyncio as redis
from flwr.common import NDArrays, Scalar

from common.interfaces import StorageBackend
from common.model_registry import ModelRegistry
from common.models import FLTaskConfig
from federated_learning.orchestrator import FederatedLearningOrchestrator
from federated_learning.simulated_client import (
    RegressionClient,
    make_regression_partitions,
)


class InMemoryStorage(StorageBackend):
    """Never reached by a crashed run; present so the registry can be constructed."""

    def __init__(self) -> None:
        self.store: dict[str, bytes] = {}

    async def upload(self, data: bytes) -> str:
        cid = "Qm" + hashlib.sha256(data).hexdigest()
        self.store[cid] = data
        return cid

    async def retrieve(self, cid: str) -> bytes:
        return self.store[cid]


class SlowRegressionClient(RegressionClient):
    """A real client that spends ``delay`` seconds per round, to widen the crash window."""

    def __init__(self, *args: Any, delay: float = 0.0, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.delay = delay

    def fit(
        self, parameters: NDArrays, config: dict[str, Scalar]
    ) -> tuple[NDArrays, int, dict[str, Scalar]]:
        time.sleep(self.delay)
        return super().fit(parameters, config)


async def main() -> None:
    redis_client = redis.from_url(
        os.environ.get("REDIS_URL", "redis://localhost:6379"), decode_responses=False
    )
    registry = ModelRegistry(redis_client, InMemoryStorage())

    num_clients = int(os.environ.get("FL_NUM_CLIENTS", "3"))
    delay = float(os.environ.get("FL_ROUND_DELAY", "0.5"))
    config = FLTaskConfig(
        task_id=os.environ.get("FL_TASK_ID", "crash-harness-task"),
        num_clients=num_clients,
        num_rounds=int(os.environ.get("FL_NUM_ROUNDS", "5")),
        round_timeout=float(os.environ.get("FL_ROUND_TIMEOUT", "60")),
    )

    partitions, _true_model = make_regression_partitions(num_clients, seed=0)
    clients = [
        SlowRegressionClient(f"client-{index}", features, labels, delay=delay)
        for index, (features, labels) in enumerate(partitions)
    ]
    orchestrator = FederatedLearningOrchestrator(
        registry,
        redis_client,
        clients,
        model_id=os.environ.get("FL_MODEL_ID", "crash-harness-model"),
        architecture="mlp",
    )
    result = await orchestrator.run_training(config)
    print(f"harness completed: model_cid={result.model_cid}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
