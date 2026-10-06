"""Runnable federated-learning service entrypoint.

Reads only the settings it needs from the environment (so it does not depend on
the blockchain/proof-submitter configuration), trains the simulated clients
through the orchestrator, and stores the resulting model in the Model Registry.
"""

import asyncio
import os

import redis.asyncio as redis

from common.model_registry import ModelRegistry
from common.models import FLTaskConfig
from common.storage.ipfs_backend import IPFSBackend
from federated_learning.orchestrator import FederatedLearningOrchestrator
from federated_learning.simulated_client import make_simulated_clients


async def main() -> None:
    redis_client = redis.from_url(
        os.environ.get("REDIS_URL", "redis://localhost:6379"), decode_responses=False
    )
    storage = IPFSBackend(node_url=os.environ.get("MODEL_STORAGE_URL", "http://localhost:5001"))
    registry = ModelRegistry(redis_client, storage)

    config = FLTaskConfig(
        task_id=os.environ.get("FL_TASK_ID", "fl-demo-task"),
        num_clients=int(os.environ.get("FL_NUM_CLIENTS", "3")),
        num_rounds=int(os.environ.get("FL_NUM_ROUNDS", "3")),
        aggregation_strategy=os.environ.get("FL_AGGREGATION_STRATEGY", "fedavg"),
        round_timeout=float(os.environ.get("FL_ROUND_TIMEOUT", "300")),
    )
    orchestrator = FederatedLearningOrchestrator(
        registry,
        redis_client,
        make_simulated_clients(config.num_clients),
        model_id=os.environ.get("FL_MODEL_ID", "fl-demo-model"),
        architecture=os.environ.get("FL_MODEL_ARCHITECTURE", "mlp"),
    )
    result = await orchestrator.run_training(config)
    print(f"FL run complete: model_cid={result.model_cid} rounds={len(result.provenance)}")


if __name__ == "__main__":
    asyncio.run(main())
