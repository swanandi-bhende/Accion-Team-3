"""Build small ONNX models for seeding, federated-learning export and tests.

Every model takes one float32 tensor named ``input`` and returns one float32 tensor
named ``output``. Weights are float32, so the model's output matches NumPy's float32
arithmetic on the same weights.
"""

import numpy as np
import onnx
from onnx import TensorProto, helper, numpy_helper

OPSET_VERSION = 13
# onnx 1.23 writes IR version 14 by default, but onnxruntime 1.30 reads up to 13.
IR_VERSION = 10


def linear_model(weight: np.ndarray, bias: np.ndarray) -> bytes:
    """Serialize a model that computes ``input @ weight + bias``.

    ``weight`` has shape (features, outputs) and ``bias`` has shape (outputs,).
    """
    weight = np.asarray(weight, dtype=np.float32)
    bias = np.asarray(bias, dtype=np.float32).reshape(-1)
    graph = helper.make_graph(
        [helper.make_node("Gemm", ["input", "weight", "bias"], ["output"])],
        "linear",
        [helper.make_tensor_value_info("input", TensorProto.FLOAT, [None, weight.shape[0]])],
        [helper.make_tensor_value_info("output", TensorProto.FLOAT, [None, weight.shape[1]])],
        initializer=[numpy_helper.from_array(weight, "weight"), numpy_helper.from_array(bias, "bias")],
    )
    return _serialize(graph)


def mlp_model(
    hidden_weight: np.ndarray,
    hidden_bias: np.ndarray,
    output_weight: np.ndarray,
    output_bias: np.ndarray,
) -> bytes:
    """Serialize a one-hidden-layer network: ``relu(input @ W1 + b1) @ W2 + b2``."""
    hidden_weight = np.asarray(hidden_weight, dtype=np.float32)
    hidden_bias = np.asarray(hidden_bias, dtype=np.float32).reshape(-1)
    output_weight = np.asarray(output_weight, dtype=np.float32)
    output_bias = np.asarray(output_bias, dtype=np.float32).reshape(-1)
    graph = helper.make_graph(
        [
            helper.make_node("Gemm", ["input", "hidden_weight", "hidden_bias"], ["hidden"]),
            helper.make_node("Relu", ["hidden"], ["activated"]),
            helper.make_node("Gemm", ["activated", "output_weight", "output_bias"], ["output"]),
        ],
        "mlp",
        [helper.make_tensor_value_info("input", TensorProto.FLOAT, [None, hidden_weight.shape[0]])],
        [helper.make_tensor_value_info("output", TensorProto.FLOAT, [None, output_weight.shape[1]])],
        initializer=[
            numpy_helper.from_array(hidden_weight, "hidden_weight"),
            numpy_helper.from_array(hidden_bias, "hidden_bias"),
            numpy_helper.from_array(output_weight, "output_weight"),
            numpy_helper.from_array(output_bias, "output_bias"),
        ],
    )
    return _serialize(graph)


def _serialize(graph: onnx.GraphProto) -> bytes:
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", OPSET_VERSION)])
    model.ir_version = IR_VERSION
    serialized: bytes = model.SerializeToString()
    return serialized
