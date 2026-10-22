"""Real models and tensors for tests that run inference.

The inference service executes ONNX models on ``.npy`` tensors, so any test that
reaches the engine needs these instead of arbitrary bytes.
"""

import base64

import numpy as np

from common.onnx_models import linear_model, mlp_model
from common.tensors import encode_tensor

FEATURES = 4


def linear_parameters(seed: int = 0) -> tuple[np.ndarray, np.ndarray]:
    """Weight (FEATURES x 1) and bias (1,) of the seeded linear model, as float32."""
    rng = np.random.default_rng(seed)
    weight = np.asarray(rng.normal(size=(FEATURES, 1)), dtype=np.float32)
    bias = np.asarray(rng.normal(size=(1,)), dtype=np.float32)
    return weight, bias


def linear_model_bytes(seed: int = 0) -> bytes:
    """ONNX bytes of the seeded linear model."""
    weight, bias = linear_parameters(seed)
    return linear_model(weight, bias)


def mlp_model_bytes(seed: int = 0) -> bytes:
    """ONNX bytes of a seeded one-hidden-layer network with ``FEATURES`` inputs."""
    rng = np.random.default_rng(seed)
    return mlp_model(
        rng.normal(size=(FEATURES, 8)),
        rng.normal(size=(8,)),
        rng.normal(size=(8, 1)),
        rng.normal(size=(1,)),
    )


def tensor(rows: int = 1, seed: int = 0) -> np.ndarray:
    """A ``rows`` x FEATURES float32 input."""
    return np.asarray(np.random.default_rng(seed).normal(size=(rows, FEATURES)), dtype=np.float32)


def tensor_bytes(rows: int = 1, seed: int = 0) -> bytes:
    """``tensor`` encoded as ``.npy`` bytes, the wire form of an inference input."""
    return encode_tensor(tensor(rows, seed))


def expected_linear_output(x: np.ndarray, seed: int = 0) -> np.ndarray:
    """What the seeded linear model should output for ``x``, computed with NumPy."""
    weight, bias = linear_parameters(seed)
    result: np.ndarray = x @ weight + bias
    return result


def tensor_b64(rows: int = 1, seed: int = 0) -> str:
    """``tensor_bytes`` as base64 text, the form a JSON inference payload carries."""
    return base64.b64encode(tensor_bytes(rows, seed)).decode("ascii")
