"""Simulated Flower clients for local federated-learning development.

These are real ``flwr.client.NumPyClient`` instances: Flower's own client
interface drives ``fit``/``evaluate``, so the orchestrator exercises genuine
Flower client code paths instead of a hand-rolled stand-in. The data is a
synthetic linear-regression problem partitioned across clients, so no network
access or external dataset download is required.

Real deployments replace these with remote clients that receive the same
``flwr`` messages over gRPC.
"""

from __future__ import annotations

import numpy as np
from flwr.client import NumPyClient


def make_regression_partitions(
    num_clients: int,
    samples_per_client: int = 64,
    features: int = 4,
    seed: int = 0,
):
    """Split one synthetic linear-regression dataset across ``num_clients``."""
    generator = np.random.default_rng(seed)
    true_weights = generator.normal(size=(features, 1))
    true_bias = float(generator.normal())
    partitions = []
    for index in range(num_clients):
        partition_rng = np.random.default_rng(seed + 1 + index)
        features_part = partition_rng.normal(size=(samples_per_client, features))
        noise = partition_rng.normal(scale=0.01, size=(samples_per_client, 1))
        labels = features_part @ true_weights + true_bias + noise
        partitions.append((features_part, labels))
    return partitions, (true_weights, true_bias)


class RegressionClient(NumPyClient):
    """A Flower client that fits a local linear-regression model."""

    def __init__(self, client_id: str, features, labels, epochs: int = 5, learning_rate: float = 0.05):
        self.client_id = client_id
        self._features = np.asarray(features, dtype=np.float64)
        self._labels = np.asarray(labels, dtype=np.float64)
        self.epochs = epochs
        self.learning_rate = learning_rate
        self._weights = np.zeros((self._features.shape[1], 1), dtype=np.float64)
        self._bias = np.zeros((1,), dtype=np.float64)

    def get_parameters(self, config):
        return [self._weights.copy(), self._bias.copy()]

    def fit(self, parameters, config):
        weights = np.asarray(parameters[0], dtype=np.float64).reshape(self._weights.shape)
        bias = np.asarray(parameters[1], dtype=np.float64).reshape(self._bias.shape)
        for _ in range(self.epochs):
            error = self._features @ weights + bias - self._labels
            weights = weights - self.learning_rate * (self._features.T @ error) / len(self._labels)
            bias = bias - self.learning_rate * np.array([error.mean()])
        self._weights, self._bias = weights, bias
        loss = float(np.mean((self._features @ weights + bias - self._labels) ** 2))
        return [weights, bias], len(self._labels), {"loss": loss}

    def evaluate(self, parameters, config):
        weights = np.asarray(parameters[0], dtype=np.float64).reshape(self._weights.shape)
        bias = np.asarray(parameters[1], dtype=np.float64).reshape(self._bias.shape)
        mse = float(np.mean((self._features @ weights + bias - self._labels) ** 2))
        return mse, len(self._labels), {"mse": mse}


def make_simulated_clients(num_clients: int, seed: int = 0, **kwargs) -> list[RegressionClient]:
    partitions, _true_model = make_regression_partitions(num_clients, seed=seed)
    return [
        RegressionClient(f"client-{index}", features, labels, **kwargs)
        for index, (features, labels) in enumerate(partitions)
    ]


def make_partitioned_client(
    partition_id: int, num_partitions: int, seed: int = 0, **kwargs
) -> RegressionClient:
    """Build the single client that owns ``partition_id`` of a shared dataset.

    Both the in-process runner and the real gRPC/SuperLink client entrypoints use
    this, so a client's data partition depends only on its assigned id and the
    total partition count -- exactly the contract a real participant has.
    """
    if not 0 <= partition_id < num_partitions:
        raise ValueError(
            f"partition_id {partition_id} out of range for {num_partitions} partitions"
        )
    partitions, _true_model = make_regression_partitions(num_partitions, seed=seed)
    features, labels = partitions[partition_id]
    return RegressionClient(f"client-{partition_id}", features, labels, **kwargs)
