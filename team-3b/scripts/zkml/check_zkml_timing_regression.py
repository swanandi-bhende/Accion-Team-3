"""Check the recorded EZKL timing and proof-size baseline for drift."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RESULT_PATH = ROOT / "data" / "zkml_spike" / "benchmark_result.json"
MAX_PROVE_SECONDS = 120.0
MAX_TIME_DRIFT = 2.0
MAX_PROOF_SIZE_DRIFT = 0.25


def main() -> int:
    result = json.loads(RESULT_PATH.read_text())
    failures = []
    for benchmark in result["benchmarks"]:
        architecture = benchmark["architecture"]
        prove_seconds = benchmark["timings_seconds"]["prove"]
        proof_size = benchmark["artifact_sizes_bytes"]["proof.json"]
        baseline = benchmark.get("baseline", {})
        baseline_prove = baseline.get("prove_seconds", prove_seconds)
        baseline_size = baseline.get("proof_size_bytes", proof_size)
        if not benchmark.get("verified"):
            failures.append(f"{architecture}: proof did not verify")
        if prove_seconds > min(MAX_PROVE_SECONDS, baseline_prove * MAX_TIME_DRIFT):
            failures.append(f"{architecture}: proof time drifted to {prove_seconds:.3f}s")
        if proof_size > baseline_size * (1.0 + MAX_PROOF_SIZE_DRIFT):
            failures.append(f"{architecture}: proof size drifted to {proof_size} bytes")
    if failures:
        print("zkML regression failure:")
        print("\n".join(f"- {failure}" for failure in failures))
        return 1
    print("zkML baselines OK: " + ", ".join(
        f"{benchmark['architecture']}={benchmark['timings_seconds']['prove']:.3f}s/{benchmark['artifact_sizes_bytes']['proof.json']}B"
        for benchmark in result["benchmarks"]
    ))
    return 0


if __name__ == "__main__":
    sys.exit(main())
