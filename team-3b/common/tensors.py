"""Encoding of inference inputs and outputs as NumPy ``.npy`` bytes.

A ``.npy`` payload carries its dtype and shape with the data. Pickle is disabled on
decode, so reading a payload can never run code.
"""

import io

import numpy as np
from numpy.typing import ArrayLike


def encode_tensor(array: ArrayLike) -> bytes:
    """Serialize an array to ``.npy`` bytes."""
    buffer = io.BytesIO()
    np.save(buffer, np.asarray(array), allow_pickle=False)
    return buffer.getvalue()


def decode_tensor(payload: bytes) -> np.ndarray:
    """Parse ``.npy`` bytes back into an array. Raises ValueError for anything else."""
    try:
        array: np.ndarray = np.load(io.BytesIO(payload), allow_pickle=False)
        return array
    except (ValueError, OSError, EOFError) as error:
        raise ValueError(f"input is not an .npy tensor: {error}") from error
