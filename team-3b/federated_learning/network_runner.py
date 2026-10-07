"""Real-network (gRPC) federated-learning runner.

The subsystem has three execution paths, deliberately kept separate:

* ``orchestrator.LocalFlowerServer`` -- in-process, calls ``client.fit`` directly.
  Fast and deterministic; used by the unit and property tests.
* this module -- Flower's *legacy gRPC* transport: a real
  ``flwr.server.start_server`` endpoint with real ``flwr.client.start_client``
  processes exchanging serialized messages over sockets. This is what the Wave 3
  checkpoint uses to prove the Flower integration works over an actual transport
  rather than in-process function calls.
* ``server_app`` / ``client_app`` -- the SuperLink path for multi-host
  production, which needs a SuperLink runtime and is documented in
  ``docs/FEDERATED_LEARNING.md``.

Both network paths share the same strategy registry and the same
``ProvenanceStrategy`` recording contract, so per-round provenance is identical
regardless of transport.

Runnable standalone (``--help`` for every flag)::

    python -m federated_learning.network_runner server --address 0.0.0.0:8080 \\
        --task-id net --num-clients 3 --num-rounds 3 --round-timeout 30 \\
        --redis-url redis://localhost:6379
    python -m federated_learning.network_runner client --address 127.0.0.1:8080 \\
        --partition-id 0 --num-partitions 3
"""

from __future__ import annotations

import argparse
import sys
from typing import Callable

from flwr.client import start_client
from flwr.server import start_server
from flwr.server.server_config import ServerConfig

from common.models import FLTaskConfig, RoundProvenance
from federated_learning.simulated_client import make_partitioned_client
from federated_learning.strategies import ProvenanceStrategy, build_strategy

DEFAULT_REDIS_URL = "redis://localhost:6379"


def build_network_strategy(
    config: FLTaskConfig,
    on_record: Callable[[RoundProvenance], None] | None = None,
    redis_url: str | None = None,
) -> ProvenanceStrategy:
    """Build the provenance-recording strategy used by the network server.

    ``on_record`` takes precedence when supplied (tests pass an in-process list);
    otherwise, with a ``redis_url``, records are appended to the same
    ``fl:provenance:<task_id>`` list the in-process runner writes, so provenance
    is transport-independent.
    """
    recorder = on_record
    if recorder is None and redis_url:
        # Imported lazily so importing this module never opens a Redis connection.
        from federated_learning.server_app import build_redis_recorder

        recorder = build_redis_recorder(config.task_id, redis_url)
    return ProvenanceStrategy(
        build_strategy(config),
        config.aggregation_strategy,
        on_record=recorder,
    )


def run_server(
    config: FLTaskConfig,
    server_address: str,
    on_record: Callable[[RoundProvenance], None] | None = None,
    redis_url: str | None = None,
):
    """Run the blocking gRPC Flower server and return its ``History``.

    ``config.round_timeout`` is enforced by Flower as a per-round timeout, so a
    client that dies mid-round is treated as a non-response and the round
    proceeds with whoever did answer (``min_fit_clients=1``). This is the
    network-path equivalent of ``orchestrator._execute_round``'s timeout.
    """
    strategy = build_network_strategy(config, on_record=on_record, redis_url=redis_url)
    return start_server(
        server_address=server_address,
        strategy=strategy,
        config=ServerConfig(num_rounds=config.num_rounds, round_timeout=config.round_timeout),
    )


def run_client(
    server_address: str,
    partition_id: int,
    num_partitions: int,
    seed: int = 0,
    max_retries: int | None = 30,
) -> None:
    """Run one blocking gRPC Flower client until the server finishes."""
    start_client(
        server_address=server_address,
        client=make_partitioned_client(partition_id, num_partitions, seed=seed).to_client(),
        max_retries=max_retries,
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m federated_learning.network_runner")
    sub = parser.add_subparsers(dest="role", required=True)

    server = sub.add_parser("server", help="run the gRPC Flower server")
    server.add_argument("--address", default="0.0.0.0:8080")
    server.add_argument("--task-id", default="fl-network-task")
    server.add_argument("--num-clients", type=int, default=3)
    server.add_argument("--num-rounds", type=int, default=3)
    server.add_argument("--strategy", default="fedavg")
    server.add_argument("--round-timeout", type=float, default=300.0)
    server.add_argument("--redis-url", default=None)

    client = sub.add_parser("client", help="run one gRPC Flower client")
    client.add_argument("--address", required=True)
    client.add_argument("--partition-id", type=int, required=True)
    client.add_argument("--num-partitions", type=int, required=True)
    client.add_argument("--seed", type=int, default=0)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    if args.role == "client":
        run_client(args.address, args.partition_id, args.num_partitions, seed=args.seed)
        return 0

    config = FLTaskConfig(
        task_id=args.task_id,
        num_clients=args.num_clients,
        num_rounds=args.num_rounds,
        aggregation_strategy=args.strategy,
        round_timeout=args.round_timeout,
    )
    history = run_server(
        config,
        args.address,
        redis_url=args.redis_url or None,
    )
    print(f"FL network run complete: rounds={len(history.losses_distributed)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
