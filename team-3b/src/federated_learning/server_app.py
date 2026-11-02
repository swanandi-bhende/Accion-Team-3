"""SuperLink server entrypoint for network (multi-host) federated learning.

This is the production transport path: participants run ``client_app.py`` and
connect to a Flower SuperLink, and this server app drives the rounds. It reuses
the same strategy registry as the in-process orchestrator and records per-round
provenance through ``ProvenanceStrategy``, persisting each record to Redis as the
round completes (Requirement 4.6).

Deployment outline:

    flwr-superlink --...            # the SuperLink control plane
    flwr-serverapp --...            # runs ``app`` from this module
    flwr-clientapp --...            # one per participant, runs client_app.py

The in-process ``LocalFlowerServer`` path remains the fast, deterministic path
for tests; this module is for real remote participants.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import redis as sync_redis
from flwr.common import Context
from flwr.server import ServerAppComponents, ServerConfig
from flwr.serverapp import ServerApp

from common.config import get_settings
from common.models import FLTaskConfig, RoundProvenance
from federated_learning.strategies import (
    ProvenanceStrategy,
    build_strategy,
    provenance_key,
)

DEFAULT_REDIS_URL = "redis://localhost:6379"


def load_task_config(run_config: dict[str, Any] | None = None) -> FLTaskConfig:
    """Build the task config from Flower's ``run_config`` (or sensible defaults)."""
    run_config = run_config or {}
    min_available = run_config.get("min-available-clients")
    return FLTaskConfig(
        task_id=run_config.get("task-id", "fl-network-task"),
        num_clients=int(run_config.get("num-clients", 3)),
        num_rounds=int(run_config.get("num-rounds", 3)),
        aggregation_strategy=run_config.get("strategy", "fedavg"),
        round_timeout=float(run_config.get("round-timeout", 300.0)),
        min_available_clients=int(min_available) if min_available is not None else None,
        trim_fraction=float(run_config.get("trim-fraction", 0.2)),
        num_malicious_clients=int(run_config.get("num-malicious-clients", 0)),
        dp_clip_norm=float(run_config.get("dp-clip-norm", 1.0)),
        dp_noise_multiplier=float(run_config.get("dp-noise-multiplier", 1.0)),
    )


def build_redis_recorder(
    task_id: str, redis_url: str = DEFAULT_REDIS_URL
) -> Callable[[RoundProvenance], None]:
    """Return a synchronous callable that appends provenance records to Redis."""
    client = sync_redis.Redis.from_url(redis_url)
    key = provenance_key(task_id)

    def record(provenance: RoundProvenance) -> None:
        client.rpush(key, provenance.model_dump_json())

    return record


def server_fn(context: Context) -> ServerAppComponents:
    """Build the provenance-recording strategy and round config for the SuperLink server.

    Task settings come from ``context.run_config`` (defaults apply when absent).
    Provenance records are appended to Redis at ``get_settings().redis_url``,
    falling back to ``redis://localhost:6379`` when it is unset.
    """
    config = load_task_config(getattr(context, "run_config", None))
    strategy = ProvenanceStrategy(
        build_strategy(config),
        config.aggregation_strategy,
        on_record=build_redis_recorder(config.task_id, get_settings().redis_url or DEFAULT_REDIS_URL),
    )
    return ServerAppComponents(
        strategy=strategy,
        # round_timeout enforces Requirement 4.5 on the network path too: a client
        # that dies mid-round counts as a non-response and the round proceeds
        # with whoever answered, rather than blocking the server indefinitely.
        config=ServerConfig(num_rounds=config.num_rounds, round_timeout=config.round_timeout),
    )


app = ServerApp(server_fn=server_fn)
