import asyncio
import hashlib
import os
from typing import cast
from unittest.mock import AsyncMock, patch

import numpy as np
import pytest

from common.exceptions import TEEUnavailableError
from common.tensors import decode_tensor
from inference.tee_runtime import TEERuntime
from tests.onnx_fixtures import (
    expected_linear_output,
    linear_model_bytes,
    tensor,
    tensor_bytes,
)


def test_uninitialized_runtime_raises() -> None:
    rt = TEERuntime("sgx")
    # Don't call initialize()

    with pytest.raises(TEEUnavailableError):
        rt.generate_attestation(b"in", b"out")

@pytest.mark.asyncio
async def test_uninitialized_execute_raises() -> None:
    rt = TEERuntime("sev")
    with pytest.raises(TEEUnavailableError):
        await rt.execute_inference(b"model", "hash", b"in", b"key")

@pytest.mark.asyncio
async def test_model_size_constraint() -> None:
    rt = TEERuntime("sgx")
    rt.initialize()

    # Create dummy bytes slightly larger than 500MB
    # We use a memoryview or bytearray so we don't actually allocate 500MB if we can avoid it.
    # Actually, allocating 500MB of zeros in python takes 500MB RAM.
    # To keep the test fast and lightweight, we mock len() or just allocate it since 500MB is fast.
    class LargeMockBytes:
        def __len__(self) -> int:
            return 500 * 1024 * 1024 + 1

    with pytest.raises(ValueError, match="exceeds 500MB"):
        await rt.execute_inference(cast(bytes, LargeMockBytes()), "hash", b"in", b"key")


@pytest.mark.asyncio
async def test_model_exactly_at_500mb_boundary_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    rt = TEERuntime("sgx")
    rt.initialize()
    assert rt.enclave is not None
    model_binary = type("BoundaryModel", (), {
        "__len__": lambda self: 500 * 1024 * 1024,
    })()
    run_isolated = AsyncMock(return_value=b"boundary-output")
    monkeypatch.setattr(rt.enclave, "run_isolated", run_isolated)

    with patch("inference.tee_runtime.hashlib.sha256") as sha256:
        sha256.return_value.hexdigest.return_value = "boundary-hash"
        output = await rt.execute_inference(model_binary, "boundary-hash", b"in", b"key")

    assert output == b"boundary-output"
    run_isolated.assert_awaited_once_with(model_binary, b"in")

@pytest.mark.asyncio
async def test_concurrent_execute_isolation() -> None:
    rt = TEERuntime("sgx")
    rt.initialize()

    sealed_key = b"0123456789abcdef0123456789abcdef"
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    aesgcm = AESGCM(sealed_key)

    model_binary = linear_model_bytes()
    model_hash = hashlib.sha256(model_binary).hexdigest()

    nonce = os.urandom(12)
    encrypted_data = nonce + aesgcm.encrypt(nonce, model_binary, None)

    # Run two inferences concurrently, each with its own input
    task1 = asyncio.create_task(
        rt.execute_inference(encrypted_data, model_hash, tensor_bytes(seed=1), sealed_key)
    )
    task2 = asyncio.create_task(
        rt.execute_inference(encrypted_data, model_hash, tensor_bytes(seed=2), sealed_key)
    )

    res1, res2 = await asyncio.gather(task1, task2)

    # Each output is the model applied to its own input, not the other request's.
    assert np.allclose(decode_tensor(res1), expected_linear_output(tensor(seed=1)), atol=1e-5)
    assert np.allclose(decode_tensor(res2), expected_linear_output(tensor(seed=2)), atol=1e-5)
    assert not np.allclose(decode_tensor(res1), decode_tensor(res2))
