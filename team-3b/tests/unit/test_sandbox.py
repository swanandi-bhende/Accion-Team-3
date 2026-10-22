import asyncio
import io
from typing import Any

import numpy as np
import pytest

from common.exceptions import ModelExecutionError
from common.tensors import decode_tensor, encode_tensor
from inference import enclave_sim
from inference.enclave_sim import EnclaveSim
from inference.sandbox import frame, read_frame
from tests.onnx_fixtures import (
    expected_linear_output,
    linear_model_bytes,
    mlp_model_bytes,
    tensor,
    tensor_bytes,
)


@pytest.mark.asyncio
async def test_linear_model_output_matches_numpy() -> None:
    x = tensor(rows=3, seed=7)

    output = decode_tensor(await EnclaveSim().run_isolated(linear_model_bytes(), encode_tensor(x)))

    assert output.shape == (3, 1)
    assert np.allclose(output, expected_linear_output(x), atol=1e-5)


@pytest.mark.asyncio
async def test_mlp_model_runs_and_returns_one_column() -> None:
    output = decode_tensor(await EnclaveSim().run_isolated(mlp_model_bytes(), tensor_bytes(rows=2)))

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
async def test_cancelled_request_kills_its_child_process(monkeypatch: pytest.MonkeyPatch) -> None:
    """A request that hits the engine's timeout must not leave its worker running."""
    started: list[Any] = []
    real_create = asyncio.create_subprocess_exec

    async def record(*args: Any, **kwargs: Any) -> Any:
        proc = await real_create(*args, **kwargs)
        started.append(proc)
        return proc

    monkeypatch.setattr(enclave_sim.asyncio, "create_subprocess_exec", record)

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(EnclaveSim().run_isolated(linear_model_bytes(), tensor_bytes()), timeout=0.01)

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
