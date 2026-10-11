"""Run real EZKL setup, proving, and verification benchmarks."""

import argparse
import json
import sys
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import ezkl
import numpy as np
import onnx
from onnx import TensorProto, helper, numpy_helper

ROOT = Path(__file__).resolve().parents[2]
OUTPUT_DIR = ROOT / "data" / "zkml_spike"


def save_model(
    path: Path,
    nodes: Sequence[onnx.NodeProto],
    inputs: Sequence[onnx.ValueInfoProto],
    outputs: Sequence[onnx.ValueInfoProto],
    initializers: Sequence[onnx.TensorProto],
    name: str,
) -> None:
    graph = helper.make_graph(nodes, name, inputs, outputs, initializers)
    model = helper.make_model(
        graph,
        producer_name="v1.8-ezkl-spike",
        opset_imports=[helper.make_opsetid("", 13)],
    )
    model.ir_version = 8
    onnx.checker.check_model(model)
    onnx.save(model, path)


def build_model(path: Path, architecture: str) -> None:
    if architecture == "linear":
        input_info = helper.make_tensor_value_info("input", TensorProto.FLOAT, [1, 2])
        output_info = helper.make_tensor_value_info("output", TensorProto.FLOAT, [1, 1])
        weights = numpy_helper.from_array(np.array([[0.5], [-0.25]], dtype=np.float32), name="weights")
        bias = numpy_helper.from_array(np.array([0.1], dtype=np.float32), name="bias")
        save_model(
            path,
            [helper.make_node("Gemm", ["input", "weights", "bias"], ["output"])],
            [input_info], [output_info], [weights, bias], "tiny_linear",
        )
        return

    if architecture == "cnn":
        input_info = helper.make_tensor_value_info("input", TensorProto.FLOAT, [1, 1, 4, 4])
        output_info = helper.make_tensor_value_info("output", TensorProto.FLOAT, [1, 1])
        conv_weights = numpy_helper.from_array(np.ones((1, 1, 3, 3), dtype=np.float32) * 0.1, name="conv_weights")
        conv_bias = numpy_helper.from_array(np.array([0.05], dtype=np.float32), name="conv_bias")
        dense_weights = numpy_helper.from_array(np.array([[0.25]], dtype=np.float32), name="dense_weights")
        dense_bias = numpy_helper.from_array(np.array([0.1], dtype=np.float32), name="dense_bias")
        save_model(
            path,
            [
                helper.make_node(
                    "Conv",
                    ["input", "conv_weights", "conv_bias"],
                    ["convolved"],
                    strides=[1, 1],
                    pads=[0, 0, 0, 0],
                ),
                helper.make_node("Relu", ["convolved"], ["activated"]),
                helper.make_node("GlobalAveragePool", ["activated"], ["pooled"]),
                helper.make_node("Flatten", ["pooled"], ["flattened"]),
                helper.make_node("Gemm", ["flattened", "dense_weights", "dense_bias"], ["output"]),
            ],
            [input_info], [output_info], [conv_weights, conv_bias, dense_weights, dense_bias], "tiny_cnn",
        )
        return

    if architecture == "transformer_small":
        input_info = helper.make_tensor_value_info("input", TensorProto.FLOAT, [1, 2, 2])
        output_info = helper.make_tensor_value_info("output", TensorProto.FLOAT, [1, 2, 2])
        projection = numpy_helper.from_array(np.eye(2, dtype=np.float32), name="projection")
        scale = numpy_helper.from_array(np.array([[[0.5]]], dtype=np.float32), name="scale")
        save_model(
            path,
            [
                helper.make_node("MatMul", ["input", "projection"], ["projected"]),
                helper.make_node("Mul", ["projected", "scale"], ["scaled"]),
                helper.make_node("Softmax", ["scaled"], ["output"], axis=-1),
            ],
            [input_info], [output_info], [projection, scale], "tiny_transformer_small",
        )
        return

    if architecture != "mlp":
        raise ValueError(f"Unsupported benchmark architecture: {architecture}")

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
    save_model(
        path,
        [
            helper.make_node("Gemm", ["input", "weights_1", "bias_1"], ["hidden"]),
            helper.make_node("Relu", ["hidden"], ["activated"]),
            helper.make_node("Gemm", ["activated", "weights_2", "bias_2"], ["output"]),
        ],
        [input_info],
        [output_info],
        [weights_1, bias_1, weights_2, bias_2],
        "tiny_mlp",
    )


def timed(label: str, function: Callable[..., Any], *args: Any) -> tuple[Any, float]:
    started = time.perf_counter()
    result = function(*args)
    return result, time.perf_counter() - started


def benchmark(architecture: str) -> dict:
    output_dir = OUTPUT_DIR / architecture
    output_dir.mkdir(parents=True, exist_ok=True)
    model_path = output_dir / f"tiny_{architecture}.onnx"
    compiled_path = output_dir / "compiled.onnx"
    settings_path = output_dir / "settings.json"
    data_path = output_dir / "input.json"
    witness_path = output_dir / "witness.json"
    srs_path = output_dir / "kzg.srs"
    vk_path = output_dir / "vk.key"
    pk_path = output_dir / "pk.key"
    proof_path = output_dir / "proof.json"

    build_model(model_path, architecture)
    if architecture in {"cnn", "transformer_small"}:
        # The bundled ezkl stub omits gen_random_data; the installed ezkl 23 provides it.
        ezkl.gen_random_data(str(model_path), str(data_path), seed=20260926, min=-1.0, max=1.0)  # type: ignore[attr-defined]
    else:
        input_data = [[1.0, 2.0]]
        data_path.write_text(json.dumps({"input_data": input_data}))

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

    return {
        "toolchain": "ezkl",
        "ezkl_version": getattr(ezkl, "__version__", "unknown"),
        "architecture": architecture,
        "model": f"tiny {architecture} ONNX model",
        "verified": bool(verified),
        "timings_seconds": timings,
        "artifact_sizes_bytes": {
            path.name: path.stat().st_size
            for path in [model_path, compiled_path, settings_path, witness_path, srs_path, vk_path, pk_path, proof_path]
        },
        "output_directory": str(output_dir.relative_to(ROOT)),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--architecture",
        action="append",
        choices=["linear", "mlp", "cnn", "transformer_small"],
        help="Architecture to benchmark; repeat for multiple architectures.",
    )
    args = parser.parse_args()
    architectures = args.architecture or ["linear", "mlp", "cnn", "transformer_small"]
    results = [benchmark(architecture) for architecture in architectures]
    result_path = OUTPUT_DIR / "benchmark_result.json"
    result_path.write_text(json.dumps({"toolchain": "ezkl", "benchmarks": results}, indent=2) + "\n")
    print(json.dumps({"toolchain": "ezkl", "benchmarks": results}, indent=2))
    return 0 if all(result["verified"] for result in results) else 1


if __name__ == "__main__":
    sys.exit(main())
