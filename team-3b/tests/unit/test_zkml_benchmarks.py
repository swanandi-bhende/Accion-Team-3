"""Recorded EZKL benchmark results and the proof-size bound checked against them."""

import json
from pathlib import Path

import pytest

from common.constants import SUPPORTED_ARCHITECTURES

SPIKE_RESULT = (
    Path(__file__).resolve().parents[2]
    / "data"
    / "zkml_spike"
    / "benchmark_result.json"
)

PROOF_SIZE_BOUND_BYTES = 64 * 1024


def test_recorded_ezkl_proofs_are_within_the_size_bound() -> None:
    if not SPIKE_RESULT.exists():
        pytest.skip("spike benchmark has not been generated")
    benchmarks = json.loads(SPIKE_RESULT.read_text())["benchmarks"]

    assert benchmarks, "the spike benchmark records no architectures"
    for benchmark in benchmarks:
        assert benchmark["baseline"]["proof_size_bytes"] <= PROOF_SIZE_BOUND_BYTES, (
            benchmark["architecture"]
        )


RESULT_PATH = (
    Path(__file__).resolve().parents[2]
    / "data"
    / "zkml_spike"
    / "benchmark_result.json"
)


@pytest.mark.parametrize("architecture", SUPPORTED_ARCHITECTURES)
def test_real_ezkl_architecture_benchmark_verified(architecture: str) -> None:
    result = json.loads(RESULT_PATH.read_text())
    benchmark = next(
        item for item in result["benchmarks"] if item["architecture"] == architecture
    )
    assert benchmark["verified"] is True
    assert benchmark["timings_seconds"]["prove"] < 120.0
