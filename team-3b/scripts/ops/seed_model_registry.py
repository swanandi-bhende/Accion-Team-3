"""Seed the Model Registry with the toy models the tests and demos use.

Runs as the ``seed-model`` init service in docker-compose.yml, so a fresh stack
has usable models without anyone running a script by hand. Both the Redis URL and
the storage endpoint come from the environment (like every other entrypoint), and
re-seeding is a no-op for a model whose registered hash already matches.
"""

import asyncio
import hashlib

import redis.asyncio as redis

from common.config import get_settings
from common.model_registry import ModelRegistry
from common.storage.ipfs_backend import IPFSBackend

MODELS = {
    "toy_linear": ("linear", b"MOCK_LINEAR_WEIGHTS_12345"),
    "toy_mlp": ("mlp", b"MOCK_MLP_WEIGHTS_67890"),
    "toy_cnn": ("cnn", b"MOCK_CNN_WEIGHTS_ABCDE"),
    "toy_transformer_small": ("transformer_small", b"MOCK_TRANSFORMER_WEIGHTS"),
}


async def seed_models() -> None:
    settings = get_settings().require("redis_url", "model_storage_url")
    redis_client = redis.from_url(settings.redis_url, decode_responses=False)
    storage = IPFSBackend(node_url=settings.model_storage_url or "http://localhost:5001")
    registry = ModelRegistry(redis_client, storage)

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
