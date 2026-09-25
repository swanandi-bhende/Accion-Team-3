"""Run a real EZKL setup, proving, and verification benchmark for a tiny MLP."""

import json
import sys
import time
from pathlib import Path

import numpy as np
import onnx
from onnx import TensorProto, helper, numpy_helper

import ezkl


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "data" / "zkml_spike"


def build_model(path: Path) -> None:
    input_info = helper.make_tensor_value_info("input", TensorProto.FLOAT, [1, 2])
    output_info = helper.make_tensor_value_info("output", TensorProto.FLOAT, [1, 1])
    weights_1 = numpy_helper.from_array(
        np.array([[0.5, -0.25], [0.75, 0.5]], dtype=np.float32), name="weights_1"
    )
    bias_1 = numpy_helper.from_array(np.array([0.1, -0.2], dtype=np.float32), name="bias_1")
    weights_2 = numpy_helper.from_array(
        np.array([[0.6], [-0.4]], dtype=np.float32), name="weights_2"
    )
    bias_2 = numpy_helper.from_array(np.array([0.05], dtype=np.float32), name="bias_2")
    graph = helper.make_graph(
        [
            helper.make_node("Gemm", ["input", "weights_1", "bias_1"], ["hidden"]),
            helper.make_node("Relu", ["hidden"], ["activated"]),
            helper.make_node("Gemm", ["activated", "weights_2", "bias_2"], ["output"]),
        ],
        "tiny_mlp",
        [input_info],
        [output_info],
        [weights_1, bias_1, weights_2, bias_2],
    )
    model = helper.make_model(
        graph,
        producer_name="v1.7-ezkl-spike",
        opset_imports=[helper.make_opsetid("", 13)],
    )
    model.ir_version = 8
    onnx.checker.check_model(model)
    onnx.save(model, path)


def timed(label: str, function, *args):
    started = time.perf_counter()
    result = function(*args)
    return result, time.perf_counter() - started


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    model_path = OUTPUT_DIR / "tiny_mlp.onnx"
    compiled_path = OUTPUT_DIR / "tiny_mlp.compiled.onnx"
    settings_path = OUTPUT_DIR / "settings.json"
    data_path = OUTPUT_DIR / "input.json"
    witness_path = OUTPUT_DIR / "witness.json"
    srs_path = OUTPUT_DIR / "kzg.srs"
    vk_path = OUTPUT_DIR / "vk.key"
    pk_path = OUTPUT_DIR / "pk.key"
    proof_path = OUTPUT_DIR / "proof.json"

    build_model(model_path)
    data_path.write_text(json.dumps({"input_data": [[1.0, 2.0]]}))

    timings = {}
    _, timings["gen_settings"] = timed("gen_settings", ezkl.gen_settings, str(model_path), str(settings_path))
    _, timings["compile_circuit"] = timed(
        "compile_circuit", ezkl.compile_circuit, str(model_path), str(compiled_path), str(settings_path)
    )

    settings = json.loads(settings_path.read_text())
    logrows = int(settings.get("logrows", 17))
    _, timings["gen_srs"] = timed("gen_srs", ezkl.gen_srs, str(srs_path), logrows)
    _, timings["setup"] = timed(
        "setup", ezkl.setup, str(compiled_path), str(vk_path), str(pk_path), str(srs_path)
    )
    _, timings["gen_witness"] = timed(
        "gen_witness",
        ezkl.gen_witness,
        str(data_path),
        str(compiled_path),
        str(witness_path),
        str(vk_path),
        str(srs_path),
    )
    _, timings["prove"] = timed(
        "prove", ezkl.prove, str(witness_path), str(compiled_path), str(pk_path), str(proof_path), str(srs_path)
    )
    verified, timings["verify"] = timed(
        "verify", ezkl.verify, str(proof_path), str(settings_path), str(vk_path), str(srs_path)
    )

    result = {
        "toolchain": "ezkl",
        "ezkl_version": getattr(ezkl, "__version__", "unknown"),
        "model": "tiny 2-layer MLP",
        "verified": bool(verified),
        "timings_seconds": timings,
        "artifact_sizes_bytes": {
            path.name: path.stat().st_size
            for path in [model_path, compiled_path, settings_path, witness_path, srs_path, vk_path, pk_path, proof_path]
        },
        "output_directory": str(OUTPUT_DIR.relative_to(ROOT)),
    }
    (OUTPUT_DIR / "benchmark_result.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
    return 0 if verified else 1


if __name__ == "__main__":
    sys.exit(main())