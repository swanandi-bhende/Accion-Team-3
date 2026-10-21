"""Runnable federated-learning service entrypoint.

Reads only the settings it needs from the environment (so it does not depend on
the blockchain/proof-submitter configuration), trains the simulated clients
through the orchestrator, and stores the resulting model in the Model Registry.
"""

import asyncio

import redis.asyncio as redis

from common.config import get_settings
from common.model_registry import ModelRegistry
from common.models import FLTaskConfig
from common.storage.ipfs_backend import IPFSBackend
from federated_learning.orchestrator import FederatedLearningOrchestrator
from federated_learning.simulated_client import make_simulated_clients


async def main() -> None:
    """Run a federated-learning training job from environment settings and store the model.

    Trains the simulated clients through the orchestrator, uploads the final model
    through the Model Registry, and prints its CID and round count.
    """
    settings = get_settings().require("redis_url", "model_storage_url")
    redis_client = redis.from_url(settings.redis_url, decode_responses=False)
    storage = IPFSBackend(node_url=settings.model_storage_url or "http://localhost:5001")
    registry = ModelRegistry(redis_client, storage)

    config = FLTaskConfig(
        task_id=settings.fl_task_id,
        num_clients=settings.fl_num_clients,
        num_rounds=settings.fl_num_rounds,
        aggregation_strategy=settings.fl_aggregation_strategy,
        round_timeout=settings.fl_round_timeout,
    )
    orchestrator = FederatedLearningOrchestrator(
        registry,
        redis_client,
        make_simulated_clients(config.num_clients),
        model_id=settings.fl_model_id,
        architecture=settings.fl_model_architecture,
    )
    result = await orchestrator.run_training(config)
    print(f"FL run complete: model_cid={result.model_cid} rounds={len(result.provenance)}")


if __name__ == "__main__":
    asyncio.run(main())
