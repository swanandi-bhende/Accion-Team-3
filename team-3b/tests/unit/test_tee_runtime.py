"""The TEE runtime and the sandboxed inference process it runs models in."""

import asyncio
import hashlib
import io
import os
from typing import Any, cast
from unittest.mock import AsyncMock, patch

import numpy as np
import pytest

from common.exceptions import ModelExecutionError, TEEUnavailableError
from common.tensors import decode_tensor, encode_tensor
from inference import enclave_sim
from inference.enclave_sim import EnclaveSim
from inference.sandbox import frame, read_frame
from inference.tee_runtime import TEERuntime
from tests.onnx_fixtures import (
    expected_linear_output,
    linear_model_bytes,
    mlp_model_bytes,
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
async def test_model_exactly_at_500mb_boundary_is_accepted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rt = TEERuntime("sgx")
    rt.initialize()
    assert rt.enclave is not None
    model_binary = type(
        "BoundaryModel",
        (),
        {
            "__len__": lambda self: 500 * 1024 * 1024,
        },
    )()
    run_isolated = AsyncMock(return_value=b"boundary-output")
    monkeypatch.setattr(rt.enclave, "run_isolated", run_isolated)

    with patch("inference.tee_runtime.hashlib.sha256") as sha256:
        sha256.return_value.hexdigest.return_value = "boundary-hash"
        output = await rt.execute_inference(
            model_binary, "boundary-hash", b"in", b"key"
        )

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
        rt.execute_inference(
            encrypted_data, model_hash, tensor_bytes(seed=1), sealed_key
        )
    )
    task2 = asyncio.create_task(
        rt.execute_inference(
            encrypted_data, model_hash, tensor_bytes(seed=2), sealed_key
        )
    )

    res1, res2 = await asyncio.gather(task1, task2)

    # Each output is the model applied to its own input, not the other request's.
    assert np.allclose(
        decode_tensor(res1), expected_linear_output(tensor(seed=1)), atol=1e-5
    )
    assert np.allclose(
        decode_tensor(res2), expected_linear_output(tensor(seed=2)), atol=1e-5
    )
    assert not np.allclose(decode_tensor(res1), decode_tensor(res2))


@pytest.mark.asyncio
async def test_linear_model_output_matches_numpy() -> None:
    x = tensor(rows=3, seed=7)

    output = decode_tensor(
        await EnclaveSim().run_isolated(linear_model_bytes(), encode_tensor(x))
    )

    assert output.shape == (3, 1)
    assert np.allclose(output, expected_linear_output(x), atol=1e-5)


@pytest.mark.asyncio
async def test_mlp_model_runs_and_returns_one_column() -> None:
    output = decode_tensor(
        await EnclaveSim().run_isolated(mlp_model_bytes(), tensor_bytes(rows=2))
    )

    assert output.shape == (2, 1)
    assert output.dtype == np.float32


@pytest.mark.asyncio
async def test_same_model_and_input_give_the_same_output() -> None:
    sim = EnclaveSim()

    first = await sim.run_isolated(linear_model_bytes(), tensor_bytes(seed=3))
    second = await sim.run_isolated(linear_model_bytes(), tensor_bytes(seed=3))

    assert first == second


@pytest.mark.asyncio
async def test_bytes_that_are_not_onnx_are_a_model_execution_error() -> None:
    with pytest.raises(ModelExecutionError):
        await EnclaveSim().run_isolated(b"MOCK_LINEAR_WEIGHTS_12345", tensor_bytes())


@pytest.mark.asyncio
async def test_input_that_is_not_a_tensor_is_a_model_execution_error() -> None:
    with pytest.raises(ModelExecutionError, match="not an .npy tensor"):
        await EnclaveSim().run_isolated(linear_model_bytes(), b"hello")


@pytest.mark.asyncio
async def test_input_with_the_wrong_width_is_a_model_execution_error() -> None:
    wrong_width = encode_tensor(np.zeros((1, 5), dtype=np.float32))

    with pytest.raises(ModelExecutionError):
        await EnclaveSim().run_isolated(linear_model_bytes(), wrong_width)


@pytest.mark.asyncio
async def test_cancelled_request_kills_its_child_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A request that hits the engine's timeout must not leave its worker running."""
    started: list[Any] = []
    real_create = asyncio.create_subprocess_exec

    async def record(*args: Any, **kwargs: Any) -> Any:
        proc = await real_create(*args, **kwargs)
        started.append(proc)
        return proc

    monkeypatch.setattr(enclave_sim.asyncio, "create_subprocess_exec", record)

    # Cancel only once the child exists. A fixed short timeout can fire before the spawn
    # finishes on a busy machine, and the test would then check nothing.
    run = asyncio.ensure_future(
        EnclaveSim().run_isolated(linear_model_bytes(), tensor_bytes())
    )
    while not started:
        await asyncio.sleep(0.01)
    run.cancel()
    with pytest.raises(asyncio.CancelledError):
        await run

    assert len(started) == 1
    assert started[0].returncode is not None
    assert started[0].returncode != 0


def test_frame_round_trips_through_read_frame() -> None:
    stream = io.BytesIO(frame(b"model") + frame(b""))

    assert read_frame(stream) == b"model"
    assert read_frame(stream) == b""


def test_truncated_frame_raises_eof() -> None:
    with pytest.raises(EOFError):
        read_frame(io.BytesIO(frame(b"model")[:-2]))


def test_decode_tensor_rejects_non_npy_bytes() -> None:
    with pytest.raises(ValueError, match="not an .npy tensor"):
        decode_tensor(b"hello")
