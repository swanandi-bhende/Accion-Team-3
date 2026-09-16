import pytest
import asyncio
import os
from inference.tee_runtime import TEERuntime
from common.exceptions import TEEUnavailableError

def test_uninitialized_runtime_raises():
    rt = TEERuntime("sgx")
    # Don't call initialize()
    
    with pytest.raises(TEEUnavailableError):
        rt.generate_attestation(b"in", b"out")

@pytest.mark.asyncio
async def test_uninitialized_execute_raises():
    rt = TEERuntime("sev")
    with pytest.raises(TEEUnavailableError):
        await rt.execute_inference(b"model", "hash", b"in", b"key")

@pytest.mark.asyncio
async def test_model_size_constraint():
    rt = TEERuntime("sgx")
    rt.initialize()
    
    # Create dummy bytes slightly larger than 500MB
    # We use a memoryview or bytearray so we don't actually allocate 500MB if we can avoid it.
    # Actually, allocating 500MB of zeros in python takes 500MB RAM.
    # To keep the test fast and lightweight, we mock len() or just allocate it since 500MB is fast.
    class LargeMockBytes:
        def __len__(self):
            return 500 * 1024 * 1024 + 1
            
    with pytest.raises(ValueError, match="exceeds 500MB"):
        await rt.execute_inference(LargeMockBytes(), "hash", b"in", b"key")

@pytest.mark.asyncio
async def test_concurrent_execute_isolation():
    rt = TEERuntime("sgx")
    rt.initialize()

    sealed_key = b"0123456789abcdef0123456789abcdef"
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    aesgcm = AESGCM(sealed_key)
    
    model_binary = b"dummy_model"
    import hashlib
    model_hash = hashlib.sha256(model_binary).hexdigest()

    nonce = os.urandom(12)
    encrypted_data = nonce + aesgcm.encrypt(nonce, model_binary, None)

    # Run two inferences concurrently
    task1 = asyncio.create_task(rt.execute_inference(encrypted_data, model_hash, b"input1", sealed_key))
    task2 = asyncio.create_task(rt.execute_inference(encrypted_data, model_hash, b"input2", sealed_key))
    
    res1, res2 = await asyncio.gather(task1, task2)
    
    # EnclaveSim echoes back the input prefixed
    assert b"input1" in res1
    assert b"input2" in res2
    assert res1 != res2
