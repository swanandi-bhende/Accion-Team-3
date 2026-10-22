"""Seed the Model Registry with the toy ONNX models the tests and demos use.

Runs as the ``seed-model`` init service in docker-compose.yml, so a fresh stack
has runnable models without anyone running a script by hand. Both the Redis URL and
the storage endpoint come from the environment (like every other entrypoint), and
re-seeding is a no-op for a model whose registered hash already matches.

Only models that can run are seeded. The cnn and transformer_small placeholders that
used to be registered as opaque bytes were removed, since they could not execute.
"""

import asyncio
import hashlib

import numpy as np
import redis.asyncio as redis

from common.config import get_settings
from common.model_registry import ModelRegistry
from common.onnx_models import linear_model, mlp_model
from common.storage.ipfs_backend import IPFSBackend

# The pipeline keys a model by its task's description hash, and the default task from
# scripts/ops/trigger_team_a_task.py has the description "linear-model". Registering the
# model under that hash lets compose settle that task. Team C's input may change which
# model a task maps to; this entry is the local stand-in until then.
TASK_DESCRIPTION = "linear-model"
TASK_MODEL_ID = hashlib.sha256(TASK_DESCRIPTION.encode()).hexdigest()

# A fixed seed keeps the weights, and therefore the bytes, identical on every run.
_RNG = np.random.default_rng(seed=0)
_TASK_RNG = np.random.default_rng(seed=1)
MODELS = {
    "toy_linear": ("linear", linear_model(_RNG.normal(size=(4, 1)), _RNG.normal(size=(1,)))),
    "toy_mlp": (
        "mlp",
        mlp_model(
            _RNG.normal(size=(4, 8)),
            _RNG.normal(size=(8,)),
            _RNG.normal(size=(8, 1)),
            _RNG.normal(size=(1,)),
        ),
    ),
    TASK_MODEL_ID: ("linear", linear_model(_TASK_RNG.normal(size=(4, 1)), _TASK_RNG.normal(size=(1,)))),
}


async def seed_models() -> None:
    """Upload toy models in ``MODELS`` unless already registered with the same hash."""
    settings = get_settings().require("redis_url", "model_storage_url")
    redis_client = redis.from_url(settings.redis_url, decode_responses=False)
    storage = IPFSBackend(node_url=settings.model_storage_url or "http://localhost:5001")
    registry = ModelRegistry(redis_client, storage)
    await registry.resume_uploads()

    print("Seeding model registry...")
    for model_id, (architecture, binary) in MODELS.items():
        existing = await registry.get(model_id)
        if existing is not None and existing.hash == hashlib.sha256(binary).hexdigest():
            print(f"{model_id}: already registered ({existing.cid})")
            continue
        cid = await registry.upload_model(model_id, binary, architecture=architecture)
        print(f"{model_id}: uploaded {architecture} model -> {cid}")
    await redis_client.close()


if __name__ == "__main__":
    asyncio.run(seed_models())
