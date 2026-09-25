import asyncio
import redis.asyncio as redis
from common.model_registry import ModelRegistry
from common.storage.ipfs_backend import IPFSBackend

async def seed_models():
    redis_client = redis.Redis(host='localhost', port=6379, db=0)
    storage = IPFSBackend(node_url="http://localhost:5001")
    registry = ModelRegistry(redis_client, storage)

    models = {
        "toy_linear": ("linear", b"MOCK_LINEAR_WEIGHTS_12345"),
        "toy_mlp": ("mlp", b"MOCK_MLP_WEIGHTS_67890"),
        "toy_cnn": ("cnn", b"MOCK_CNN_WEIGHTS_ABCDE"),
        "toy_transformer_small": ("transformer_small", b"MOCK_TRANSFORMER_WEIGHTS"),
    }

    print("Seeding local model registry...")
    for model_id, (architecture, binary) in models.items():
        try:
            print(f"Uploading {model_id}...")
            cid = await registry.upload_model(model_id, binary, architecture=architecture)
            print(f"Success! {model_id} -> CID: {cid}")
        except Exception as e:
            print(f"Failed to upload {model_id}: {e}")

    await redis_client.aclose()

if __name__ == "__main__":
    asyncio.run(seed_models())
