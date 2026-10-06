"""Serialization and reference aggregation for federated-learning weights.

Kept separate from the orchestrator and the strategy registry so both can share
it without a circular import, and so the reference aggregation arithmetic can be
tested independently of Flower.
"""

from __future__ import annotations

import hashlib
import io
from typing import Sequence

import numpy as np


def serialize_parameters(parameters: Sequence[np.ndarray]) -> bytes:
    """Deterministically serialize a parameter list to bytes for storage."""
    buffer = io.BytesIO()
    np.save(buffer, np.asarray([len(parameters)], dtype=np.int64))
    for parameter in parameters:
        np.save(buffer, np.asarray(parameter, dtype=np.float64))
    return buffer.getvalue()


def deserialize_parameters(payload: bytes) -> list[np.ndarray]:
    buffer = io.BytesIO(payload)
    count = int(np.load(buffer)[0])
    return [np.load(buffer) for _ in range(count)]


def parameters_hash(parameters: Sequence[np.ndarray]) -> str:
    return hashlib.sha256(serialize_parameters(parameters)).hexdigest()


def weighted_average(
    updates: Sequence[Sequence[np.ndarray]], weights: Sequence[int] | None = None
) -> list[np.ndarray]:
    """Elementwise (weighted) mean of per-client parameter lists.

    ``updates`` is a sequence of per-client parameter lists (the same shape
    Flower's FedAvg aggregates) and the return value is the aggregated parameter
    list. This is our own reference implementation of FedAvg's arithmetic, kept
    independent of Flower so aggregation correctness can be checked from first
    principles rather than trusting the framework's default.
    """
    if not updates:
        raise ValueError("weighted_average requires at least one update")
    if weights is None:
        weights = [1] * len(updates)
    if len(weights) != len(updates):
        raise ValueError("weights and updates must have equal length")
    total = float(sum(weights))
    if total <= 0:
        raise ValueError("weights must sum to a positive value")

    reference_shapes = [np.asarray(parameter).shape for parameter in updates[0]]
    accumulator = [np.zeros(shape, dtype=np.float64) for shape in reference_shapes]

    for update, weight in zip(updates, weights):
        arrays = [np.asarray(parameter, dtype=np.float64) for parameter in update]
        if [array.shape for array in arrays] != reference_shapes:
            raise ValueError("all updates must share the same parameter shapes")
        for total_array, array in zip(accumulator, arrays):
            total_array += array * weight

    return [total_array / total for total_array in accumulator]
