import asyncio
import redis.asyncio as redis
from common.model_registry import ModelRegistry
from common.storage.ipfs_backend import IPFSBackend

async def seed_models():
    redis_client = redis.Redis(host='localhost', port=6379, db=0)
    storage = IPFSBackend(node_url="http://localhost:5001")
    registry = ModelRegistry(redis_client, storage)

    models = {
        "toy_linear": b"MOCK_LINEAR_WEIGHTS_12345",
        "toy_mlp": b"MOCK_MLP_WEIGHTS_67890",
        "toy_cnn": b"MOCK_CNN_WEIGHTS_ABCDE"
    }

    print("Seeding local model registry...")
    for model_id, binary in models.items():
        try:
            print(f"Uploading {model_id}...")
            cid = await registry.upload_model(model_id, binary)
            print(f"Success! {model_id} -> CID: {cid}")
        except Exception as e:
            print(f"Failed to upload {model_id}: {e}")

    await redis_client.aclose()

if __name__ == "__main__":
    asyncio.run(seed_models())
