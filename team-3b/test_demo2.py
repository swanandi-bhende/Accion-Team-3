import pytest
import os
os.environ['TEE_MODE'] = 'sgx'
os.environ['TEE_SEALED_KEY_PATH'] = './data/sealed_key.bin'
os.environ['REDIS_URL'] = 'redis://localhost:6379'
os.environ['MODEL_STORAGE_BACKEND'] = 'ipfs'
os.environ['MODEL_STORAGE_URL'] = 'http://localhost:5001'
os.environ['WEB3_WS_URL'] = 'ws://localhost:8545'
os.environ['WEB3_RPC_URL'] = 'http://localhost:8545'
os.environ['TASK_MANAGER_ADDRESS'] = '0x0000000000000000000000000000000000000000'
os.environ['ESCROW_ADDRESS'] = '0x0000000000000000000000000000000000000000'
os.environ['PROOF_SUBMITTER_PRIVATE_KEY'] = '0x0000000000000000000000000000000000000000000000000000000000000000'
os.environ['ZKML_PROVING_KEY_STORE'] = './data/proving_keys'
os.environ['ZKML_SUPPORTED_ARCHITECTURES'] = 'linear'

from httpx import AsyncClient, ASGITransport
from inference.app import app

@pytest.mark.asyncio
async def test_demo():
    print("Testing ASGITransport lifespan")
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            print(f"Inside client, has inference_engine: {hasattr(app.state, 'inference_engine')}")
    except Exception as e:
        print(f"Exception during client: {e}")
