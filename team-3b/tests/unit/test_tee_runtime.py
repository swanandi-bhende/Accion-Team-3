import asyncio
import os
from unittest.mock import AsyncMock, patch

import pytest

from common.exceptions import TEEUnavailableError
from inference.tee_runtime import TEERuntime


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
async def test_model_exactly_at_500mb_boundary_is_accepted():
    rt = TEERuntime("sgx")
    rt.initialize()
    model_binary = type("BoundaryModel", (), {
        "__len__": lambda self: 500 * 1024 * 1024,
    })()
    rt.enclave.run_isolated = AsyncMock(return_value=b"boundary-output")

    with patch("inference.tee_runtime.hashlib.sha256") as sha256:
        sha256.return_value.hexdigest.return_value = "boundary-hash"
        output = await rt.execute_inference(model_binary, "boundary-hash", b"in", b"key")

    assert output == b"boundary-output"
    rt.enclave.run_isolated.assert_awaited_once_with(model_binary, b"in")

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
