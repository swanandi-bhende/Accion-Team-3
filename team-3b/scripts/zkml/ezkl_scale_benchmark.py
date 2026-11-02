"""Measure EZKL setup, proving and verification for an MLP at a chosen width.

The tiny benchmark in ezkl_mlp_benchmark.py uses 2x2 models, which says nothing about production
size. This script builds a two-layer MLP (width -> width -> 4) and reports how proof time, proof
size, key sizes and peak memory grow with the parameter count. Run one width per process, so the
peak memory figure belongs to that width alone.

Proving keys are large, so artifacts go to ``--work-dir``, which should be outside the repo. The
summary JSON goes to ``data/zkml_spike/scale/``.
"""

import argparse
import json
import resource
import sys
from pathlib import Path

import ezkl
import numpy as np
import onnx
from onnx import TensorProto, helper, numpy_helper

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ezkl_mlp_benchmark import (  # noqa: E402  # the timing helper shared with the tiny benchmark
    ROOT,
    timed,
)

OUTPUT_DIR = ROOT / "data" / "zkml_spike" / "scale"
OUTPUTS = 4


def build_mlp(path: Path, width: int) -> int:
    """Write the MLP to ``path`` and return its parameter count."""
    rng = np.random.default_rng(width)
    tensors = {
        "w1": np.asarray(rng.normal(0, 1 / np.sqrt(width), (width, width)), dtype=np.float32),
        "b1": np.zeros(width, dtype=np.float32),
        "w2": np.asarray(rng.normal(0, 1 / np.sqrt(width), (width, OUTPUTS)), dtype=np.float32),
        "b2": np.zeros(OUTPUTS, dtype=np.float32),
    }
    graph = helper.make_graph(
        [
            helper.make_node("Gemm", ["input", "w1", "b1"], ["hidden"]),
            helper.make_node("Relu", ["hidden"], ["activated"]),
            helper.make_node("Gemm", ["activated", "w2", "b2"], ["output"]),
        ],
        "scale_mlp",
        [helper.make_tensor_value_info("input", TensorProto.FLOAT, [1, width])],
        [helper.make_tensor_value_info("output", TensorProto.FLOAT, [1, OUTPUTS])],
        [numpy_helper.from_array(array, name=name) for name, array in tensors.items()],
    )
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 13)])
    onnx.checker.check_model(model)
    onnx.save(model, path)
    return sum(array.size for array in tensors.values())


def peak_memory_mb() -> float:
    """Peak resident memory of this process. macOS reports bytes, Linux reports kilobytes."""
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak / 1024**2 if sys.platform == "darwin" else peak / 1024


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--width", type=int, required=True, help="Input and hidden width of the MLP.")
    parser.add_argument("--work-dir", type=Path, required=True, help="Scratch directory for large artifacts.")
    args = parser.parse_args()

    work = args.work_dir / f"width_{args.width}"
    work.mkdir(parents=True, exist_ok=True)
    model_path = work / "model.onnx"
    data_path = work / "input.json"
    settings_path = work / "settings.json"
    compiled_path = work / "compiled.onnx"
    srs_path = work / "kzg.srs"
    vk_path = work / "vk.key"
    pk_path = work / "pk.key"
    witness_path = work / "witness.json"
    proof_path = work / "proof.json"

    parameters = build_mlp(model_path, args.width)
    rng = np.random.default_rng(0)
    data_path.write_text(json.dumps({"input_data": np.asarray(rng.normal(size=(1, args.width))).tolist()}))

    timings: dict[str, float] = {}
    _, timings["gen_settings"] = timed("gen_settings", ezkl.gen_settings, str(model_path), str(settings_path))
    _, timings["compile_circuit"] = timed(
        "compile_circuit", ezkl.compile_circuit, str(model_path), str(compiled_path), str(settings_path)
    )
    logrows = int(json.loads(settings_path.read_text()).get("logrows", 17))
    _, timings["gen_srs"] = timed("gen_srs", ezkl.gen_srs, str(srs_path), logrows)
    _, timings["setup"] = timed("setup", ezkl.setup, str(compiled_path), str(vk_path), str(pk_path), str(srs_path))
    _, timings["gen_witness"] = timed(
        "gen_witness", ezkl.gen_witness, str(data_path), str(compiled_path), str(witness_path), str(vk_path), str(srs_path)
    )
    _, timings["prove"] = timed(
        "prove", ezkl.prove, str(witness_path), str(compiled_path), str(pk_path), str(proof_path), str(srs_path)
    )
    verified, timings["verify"] = timed(
        "verify", ezkl.verify, str(proof_path), str(settings_path), str(vk_path), str(srs_path)
    )

    result = {
        "ezkl_version": getattr(ezkl, "__version__", "unknown"),
        "width": args.width,
        "parameters": parameters,
        "weight_bytes_float32": parameters * 4,
        "logrows": logrows,
        "verified": bool(verified),
        "timings_seconds": timings,
        "artifact_sizes_bytes": {path.name: path.stat().st_size for path in [proof_path, pk_path, vk_path, srs_path]},
        "peak_memory_mb": round(peak_memory_mb(), 1),
    }
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / f"width_{args.width}.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
    return 0 if verified else 1


if __name__ == "__main__":
    sys.exit(main())
