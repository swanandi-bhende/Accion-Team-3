"""Timed checks for the latency requirements: 5.1 (event detection), 9.1 (verification) and 1.1 (inference).

Each test runs the real code path and asserts the spec's bound, so a slowdown fails the test.
What the tests do not cover, and what they therefore do not prove:

- The chain is a fake. The test decides when an event is "mined", so 5.1 measures the listener's
  polling and processing, not block production or RPC latency on Galileo.
- Verification uses the development proof backend, not EZKL. EZKL verification times are in
  docs/STATUS.md (scale ladder).
- The inference model is one 502 MB MatMul. It exercises the 500 MB size and the sandboxed
  runtime, not a real model's operator mix.
"""

import asyncio
import time
from types import SimpleNamespace

import fakeredis.aioredis as fakeredis
import numpy as np
import pytest
from onnx import TensorProto, helper, numpy_helper

from common.model_registry import ModelRegistry
from common.models import InferenceRequest
from common.tensors import decode_tensor, encode_tensor
from event_listener.listener import EventListener
from inference.engine import InferenceEngine
from inference.tee_runtime import TEERuntime
from tests.property.test_model_registry_prop import MockStorageBackend
from tests.property.test_verification_interface import (
    _build_interface,
    _tee_proof,
    _trust,
    _verify,
    _zkml_proof,
)
from tests.unit.test_event_listener_unit import (
    GENESIS_A,
    FakeChain,
    FakeEvents,
    created_event,
)

DETECTION_BOUND_S = 15.0
VERIFICATION_BOUND_S = 5.0
INFERENCE_BOUND_S = 30.0
LARGE_INPUTS = 1000
LARGE_OUTPUTS = 125_500  # 1000 x 125500 float32 weights is 502 MB, above the 500 MB target


@pytest.mark.asyncio
async def test_a_task_created_event_is_detected_within_15_seconds() -> None:
    task_id = bytes.fromhex("cd" * 32)
    chain = FakeChain(GENESIS_A, head=100)
    created = FakeEvents()
    listener = EventListener(
        web3=chain,
        task_manager=SimpleNamespace(events=SimpleNamespace(TaskCreated=created, BidAccepted=FakeEvents())),
        escrow=SimpleNamespace(events=SimpleNamespace(TaskFunded=FakeEvents())),
        redis_client=fakeredis.FakeRedis(decode_responses=False),
    )
    poll = asyncio.create_task(listener.start(poll_interval=1.0))

    # Let the listener poll a few times before the event is mined, as on a live chain.
    await asyncio.sleep(2.5)
    created.entries.append(created_event(task_id, block=101))
    chain.eth.block_number = 101
    mined_at = time.perf_counter()

    key = listener._task_id(task_id)
    while "created" not in listener._task_events.get(key, {}):
        if time.perf_counter() - mined_at > DETECTION_BOUND_S * 2:
            break
        await asyncio.sleep(0.01)
    detected_in = time.perf_counter() - mined_at
    listener.stop()
    await asyncio.wait_for(poll, timeout=5.0)

    detected = listener._task_events[key]["created"]
    print(f"\n[5.1] TaskCreated detected {detected_in:.3f} s after it was mined (bound {DETECTION_BOUND_S:.0f} s)")
    assert detected["block_number"] == 101
    assert detected_in < DETECTION_BOUND_S, f"TaskCreated detected after {detected_in:.2f} s"


async def _valid_request(proof_format: str, tee_verifier: object) -> tuple[bytes, dict[str, str]]:
    """A fresh valid proof of ``proof_format``, with the TEE signer trusted where needed."""
    if proof_format == "tee_attestation":
        proof_bytes, public_inputs, enclave = _tee_proof()
        _trust(tee_verifier, enclave)  # type: ignore[arg-type]
        return proof_bytes, public_inputs
    proof_bytes, public_inputs = _zkml_proof()
    return proof_bytes, public_inputs


@pytest.mark.asyncio
@pytest.mark.parametrize("proof_format", ["tee_attestation", "zkml_snark"])
async def test_verification_returns_within_5_seconds(proof_format: str) -> None:
    interface, tee_verifier, _storage = _build_interface()
    durations: list[float] = []
    for _ in range(20):
        proof_bytes, public_inputs = await _valid_request(proof_format, tee_verifier)
        response, seconds = await _verify(interface, proof_format, public_inputs, proof_bytes=proof_bytes)
        assert response.result is True, response.reason_code
        durations.append(seconds)

    print(f"\n[9.1] {proof_format}: slowest of 20 verifications {max(durations):.4f} s (bound {VERIFICATION_BOUND_S:.0f} s)")
    assert max(durations) < VERIFICATION_BOUND_S, f"slowest verification {max(durations):.3f} s"


def large_model_bytes() -> bytes:
    """A one-layer ONNX model whose serialized form is about 502 MB."""
    weights = np.random.default_rng(0).random((LARGE_INPUTS, LARGE_OUTPUTS), dtype=np.float32)
    graph = helper.make_graph(
        [helper.make_node("MatMul", ["input", "weights"], ["output"])],
        "large_matmul",
        [helper.make_tensor_value_info("input", TensorProto.FLOAT, [1, LARGE_INPUTS])],
        [helper.make_tensor_value_info("output", TensorProto.FLOAT, [1, LARGE_OUTPUTS])],
        [numpy_helper.from_array(weights, name="weights")],
    )
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 13)])
    # The installed onnx writes IR version 14, but the installed onnxruntime reads up to 13.
    model.ir_version = 10
    return bytes(model.SerializeToString())


@pytest.mark.asyncio
async def test_inference_on_a_500_mb_model_returns_within_30_seconds() -> None:
    redis_client = fakeredis.FakeRedis(decode_responses=False)
    storage = MockStorageBackend()
    registry = ModelRegistry(redis_client, storage)
    model = large_model_bytes()
    assert len(model) >= 500_000_000
    await registry.register("large_model", await storage.upload(model), model)
    tee = TEERuntime("sgx")
    tee.initialize()
    engine = InferenceEngine(registry, tee, redis_client, timeout_s=INFERENCE_BOUND_S)
    input_data = np.random.default_rng(1).random((1, LARGE_INPUTS), dtype=np.float32)

    started = time.perf_counter()
    receipt = await engine.execute(InferenceRequest(model_id="large_model", input_data=encode_tensor(input_data)))
    elapsed = time.perf_counter() - started

    print(f"\n[1.1] 502 MB model inference {elapsed:.2f} s (bound {INFERENCE_BOUND_S:.0f} s)")
    assert elapsed < INFERENCE_BOUND_S, f"500 MB inference took {elapsed:.2f} s"
    assert decode_tensor(receipt.output).shape == (1, LARGE_OUTPUTS)
