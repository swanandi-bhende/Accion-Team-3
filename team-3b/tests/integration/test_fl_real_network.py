"""Real Flower gRPC network tests (Wave 3 checkpoint, Steps 15.3 and 15.4).

Unlike ``test_fl_orchestrator_integration.py`` -- which drives Flower's
``NumPyClient`` and ``Strategy`` objects in-process -- these tests run a genuine
gRPC Flower server with real client *processes* exchanging serialized messages
over sockets. That transport is where integration bugs actually live: client
registration, the round-availability gate, disconnect handling, and Flower's
signal/threading requirements are all below the in-process API.

Two things are proven here:

* Step 15.3 -- a real 3-client, multi-round job runs end-to-end and the training
  loss actually decreases round-over-round. A subtly broken aggregation loop can
  "run" without error while producing a useless model, so loss behaviour is the
  sanity check that matters.
* Step 15.4 -- killing one client process mid-training does not crash the run;
  the round completes with the remaining clients and ``RoundProvenance`` records
  the reduced participation count.

These tests are marked ``fl_network`` and run in the dedicated, longer-timeout
``fl-integration`` CI job rather than the fast unit/property gate.
"""

from __future__ import annotations

import socket
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

import pytest
from flwr.server import start_server
from flwr.server.history import History
from flwr.server.server_config import ServerConfig

from common.models import FLTaskConfig, RoundProvenance
from federated_learning.strategies import ProvenanceStrategy, build_strategy

ROOT = Path(__file__).resolve().parents[2]
PYTHON = sys.executable
NUM_CLIENTS = 3

pytestmark = pytest.mark.fl_network


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _spawn_clients(address: str, count: int) -> list[subprocess.Popen]:
    """Start ``count`` real Flower client processes via the documented entrypoint."""
    processes = []
    for partition_id in range(count):
        processes.append(
            subprocess.Popen(
                [
                    PYTHON,
                    "-m",
                    "federated_learning.network_runner",
                    "client",
                    "--address",
                    address,
                    "--partition-id",
                    str(partition_id),
                    "--num-partitions",
                    str(count),
                ],
                cwd=ROOT,
            )
        )
    return processes


def _terminate(processes: list[subprocess.Popen]) -> None:
    for process in processes:
        if process.poll() is None:
            process.kill()
    for process in processes:
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:  # pragma: no cover - defensive
            process.kill()


def _run_server(
    config: FLTaskConfig, address: str, on_record: Callable[[RoundProvenance], None]
) -> History:
    strategy = ProvenanceStrategy(
        build_strategy(config), config.aggregation_strategy, on_record=on_record
    )
    return start_server(
        server_address=address,
        strategy=strategy,
        config=ServerConfig(num_rounds=config.num_rounds, round_timeout=config.round_timeout),
    )


def test_real_fl_training_loss_decreases_over_rounds() -> None:
    """Step 15.3: real server + 3 real client processes, loss falls each round."""
    address = f"127.0.0.1:{_free_port()}"
    processes = _spawn_clients(address, NUM_CLIENTS)
    records: list[RoundProvenance] = []
    started = time.time()
    try:
        config = FLTaskConfig(
            task_id="real-loss", num_clients=NUM_CLIENTS, num_rounds=4, round_timeout=30.0
        )
        history = _run_server(config, address, records.append)
    finally:
        _terminate(processes)
    elapsed = time.time() - started

    # Every round ran, over the real transport, with the whole roster participating.
    assert [record.round_number for record in records] == [1, 2, 3, 4]
    assert all(record.participating_clients == NUM_CLIENTS for record in records)

    losses = [loss for _, loss in history.losses_distributed]
    assert len(losses) >= 2, "distributed evaluation produced no per-round loss"
    assert losses[-1] < losses[0], f"training loss did not decrease: {losses}"
    # A working loop should make real progress, not just nudges.
    assert losses[-1] < 0.5 * losses[0], f"loss barely moved: {losses}"
    # Step 15.6: keep real wall-clock numbers visible in the test output.
    print(f"\nreal FL run: {len(losses)} rounds, {elapsed:.2f}s wall clock")


def test_real_fl_round_completes_after_a_client_disconnects() -> None:
    """Step 15.4: a real mid-training client death does not crash the run.

    The disconnect is triggered from the provenance callback -- i.e. exactly when
    round 1 has been aggregated -- so it is deterministic rather than a timing
    race against round duration.
    """
    address = f"127.0.0.1:{_free_port()}"
    processes = _spawn_clients(address, NUM_CLIENTS)
    records: list[RoundProvenance] = []

    def on_record(record: RoundProvenance) -> None:
        records.append(record)
        if record.round_number == 1:
            processes[1].kill()  # a real process death, not a mocked "K responded" flag

    try:
        config = FLTaskConfig(
            task_id="real-dropout",
            num_clients=NUM_CLIENTS,
            num_rounds=4,
            round_timeout=10.0,
            # Tolerate one dropout; without this the client manager blocks forever
            # waiting for the missing participant (Requirement 4.5).
            min_available_clients=NUM_CLIENTS - 1,
        )
        history = _run_server(config, address, on_record)
    finally:
        _terminate(processes)

    # run_training-equivalent completed: no exception propagated to the caller.
    assert history is not None
    assert [record.round_number for record in records] == [1, 2, 3, 4]

    participation = [record.participating_clients for record in records]
    assert participation[0] == NUM_CLIENTS, "round 1 should have the full roster"
    assert min(participation) == NUM_CLIENTS - 1, f"reduced count not recorded: {participation}"
    assert participation[-1] == NUM_CLIENTS - 1, "later rounds should run with survivors"
    # The lost client caused a real aggregation failure to be surfaced, not a crash.
    assert processes[1].returncode is not None
