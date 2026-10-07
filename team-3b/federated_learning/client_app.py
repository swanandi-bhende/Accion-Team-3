"""SuperLink client entrypoint run by each federated-learning participant.

Wraps the same ``NumPyClient`` used by the in-process orchestrator into a
``ClientApp`` so it can run as a real remote participant. Each participant is
identified by Flower's ``partition-id`` node config and holds its own data
partition.

Deployment outline (full steps in docs/FEDERATED_LEARNING.md):

    flwr-clientapp --...            # one process per participant

Real deployments would replace the synthetic dataset with the participant's
local data; the Flower client interface does not change.
"""

from __future__ import annotations

from flwr.client import ClientApp
from flwr.common import Context

from federated_learning.simulated_client import make_partitioned_client


def client_fn(context: Context):
    node_config = getattr(context, "node_config", {}) or {}
    partition_id = int(node_config.get("partition-id", 0))
    num_partitions = int(node_config.get("num-partitions", 1))
    return make_partitioned_client(partition_id, num_partitions).to_client()


app = ClientApp(client_fn)
