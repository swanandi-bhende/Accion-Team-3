"""The zkML proof-size bound against the zkML spike measurements.

The design document promised a constant-size proof of about 256 bytes. The spike's EZKL
proofs measure 17-43 KB, and no measured path reaches 256 bytes. Engineering therefore
adopted option A in docs/ZKML_SPIKE.md, "Proof size: spec and toolchain disagree": a
bounded size, at most 64 KiB, which the spec owner still has to ratify.

This checks the recorded spike numbers, not a live proof. A regenerated benchmark that
exceeds the bound fails here.
"""

import json
from pathlib import Path

import pytest

SPIKE_RESULT = Path(__file__).resolve().parents[2] / "data" / "zkml_spike" / "benchmark_result.json"
PROOF_SIZE_BOUND_BYTES = 64 * 1024


def test_recorded_ezkl_proofs_are_within_the_size_bound() -> None:
    if not SPIKE_RESULT.exists():
        pytest.skip("spike benchmark has not been generated")
    benchmarks = json.loads(SPIKE_RESULT.read_text())["benchmarks"]

    assert benchmarks, "the spike benchmark records no architectures"
    for benchmark in benchmarks:
        assert benchmark["baseline"]["proof_size_bytes"] <= PROOF_SIZE_BOUND_BYTES, benchmark["architecture"]
