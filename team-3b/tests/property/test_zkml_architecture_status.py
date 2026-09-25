import json
from pathlib import Path

import pytest

from common.constants import SUPPORTED_ARCHITECTURES


RESULT_PATH = Path(__file__).resolve().parents[2] / "data" / "zkml_spike" / "benchmark_result.json"


@pytest.mark.parametrize("architecture", SUPPORTED_ARCHITECTURES)
def test_real_ezkl_architecture_benchmark_verified(architecture):
    result = json.loads(RESULT_PATH.read_text())
    benchmark = next(item for item in result["benchmarks"] if item["architecture"] == architecture)
    assert benchmark["verified"] is True
    assert benchmark["timings_seconds"]["prove"] < 120.0