"""The EZKL backend, end to end on a tiny linear model: bundles, proofs, checks, and outcomes.

These tests run the real EZKL toolchain, so they are skipped where the ``ezkl`` package is
not installed (see requirements/zkml.txt). The bundle is built once per module.
"""

import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import fakeredis.aioredis as fakeredis
import numpy as np
import onnxruntime
import pytest
from google.protobuf.message import DecodeError  # type: ignore[import-untyped]

# Look the package up without importing it. Importing ezkl into the test process loads its
# native threads there, and they abort the interpreter at exit. The EZKL work runs in a child.
if importlib.util.find_spec("ezkl") is None:
    pytest.skip("the ezkl package is not installed (requirements/zkml.txt)", allow_module_level=True)

from common.exceptions import (
    ProofInconsistentError,
    ProvingBackendUnavailableError,
    ProvingKeysUnavailableError,
)
from common.model_registry import ModelRegistry
from common.models import ExecutionReceipt, InferenceRequest
from common.onnx_models import linear_model
from common.tensors import encode_tensor
from inference.pipeline import OUTCOME_DROPPED_PROOF_KEYS_MISSING, Pipeline, TaskDropped
from inference.tee_runtime import TEERuntime
from proof_generator import ezkl_backend
from proof_generator.zkml_prover import ZkMLProver
from tests.property.test_model_registry_prop import MockStorageBackend
from verification.verifiers import ConcreteZkMLVerifier, serialize_proof_artifact

FEATURES = 4
X = np.array([[0.5, -1.0, 2.0, 0.25]], dtype=np.float32)


@pytest.fixture(scope="module")
def model_bytes() -> bytes:
    rng = np.random.default_rng(7)
    return linear_model(rng.normal(size=(FEATURES, 1)), rng.normal(size=(1,)))


@pytest.fixture(scope="module")
def model_hash(model_bytes: bytes) -> str:
    return hashlib.sha256(model_bytes).hexdigest()


@pytest.fixture(scope="module")
def store(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("proving_keys")


@pytest.fixture(scope="module")
def bundle(model_bytes: bytes, model_hash: str, store: Path) -> ezkl_backend.ProvingBundle:
    return ezkl_backend.build_bundle(store, model_bytes, model_hash, "linear")


def run_model(model_bytes: bytes, x: np.ndarray) -> np.ndarray:
    session = onnxruntime.InferenceSession(model_bytes, providers=["CPUExecutionProvider"])
    outputs: list[np.ndarray] = session.run(None, {session.get_inputs()[0].name: x})
    return outputs[0]


def receipt_for(model_hash: str, input_bytes: bytes, output: np.ndarray) -> ExecutionReceipt:
    output_bytes = encode_tensor(output)
    return ExecutionReceipt(
        execution_id="execution-1",
        model_id=model_hash,
        model_cid="QmModel",
        input_hash=hashlib.sha256(input_bytes).hexdigest(),
        output_hash=hashlib.sha256(output_bytes).hexdigest(),
        output=output_bytes,
        timestamp=1.0,
    )


async def registry_with(model_bytes: bytes, model_hash: str) -> ModelRegistry:
    registry = ModelRegistry(fakeredis.FakeRedis(decode_responses=False), MockStorageBackend())
    await registry.upload_model(model_hash, model_bytes, architecture="linear")
    return registry


# --- bundles -----------------------------------------------------------------------------


def test_bundle_keeps_a_smaller_circuit_than_calibration_chose(bundle: ezkl_backend.ProvingBundle) -> None:
    metadata = json.loads((bundle.directory / "bundle.json").read_text())

    assert (bundle.logrows, bundle.num_inner_cols) in [tuple(c) for c in metadata["candidates_verified"]]
    assert bundle.logrows < metadata["calibrated_logrows"]
    assert bundle.pk_path.stat().st_size < 64 * 1024 * 1024


def test_building_again_reuses_the_stored_bundle(
    bundle: ezkl_backend.ProvingBundle, model_bytes: bytes, model_hash: str, store: Path
) -> None:
    modified = bundle.pk_path.stat().st_mtime_ns

    again = ezkl_backend.build_bundle(store, model_bytes, model_hash, "linear")

    assert again.directory == bundle.directory
    assert bundle.pk_path.stat().st_mtime_ns == modified


def test_a_model_that_cannot_be_built_leaves_no_partial_bundle(store: Path) -> None:
    before = set(store.iterdir())

    with pytest.raises(DecodeError):
        ezkl_backend.build_bundle(store, b"not an onnx model", "ab" * 32, "linear")

    assert set(store.iterdir()) == before


def test_loading_a_missing_bundle_is_reported(store: Path) -> None:
    with pytest.raises(ProvingKeysUnavailableError):
        ezkl_backend.load_bundle(store, "cd" * 32)


# --- proofs ------------------------------------------------------------------------------


def test_proof_verifies_and_reports_the_circuit_output(bundle: ezkl_backend.ProvingBundle) -> None:
    proof = ezkl_backend.prove(bundle, X)

    assert ezkl_backend.verify(bundle, proof.proof_bytes)
    assert proof.outputs.shape == (1, 1)


def test_altered_proof_fails_verification(bundle: ezkl_backend.ProvingBundle) -> None:
    proof = ezkl_backend.prove(bundle, X)
    payload = json.loads(proof.proof_bytes)
    middle = len(payload["proof"]) // 2
    payload["proof"][middle] = (payload["proof"][middle] + 1) % 256

    assert ezkl_backend.verify(bundle, json.dumps(payload).encode()) is False


def test_malformed_proof_bytes_fail_verification_without_raising(bundle: ezkl_backend.ProvingBundle) -> None:
    assert ezkl_backend.verify(bundle, b"not a proof") is False


def test_output_check_accepts_quantization_error_and_rejects_a_real_gap() -> None:
    tee_output = np.array([[0.5]], dtype=np.float32)

    ezkl_backend.check_output_consistency(np.array([[0.5 + 1e-7]]), tee_output, quantization_scale=13)
    with pytest.raises(ProofInconsistentError):
        ezkl_backend.check_output_consistency(np.array([[0.6]]), tee_output, quantization_scale=13)
    with pytest.raises(ProofInconsistentError):
        ezkl_backend.check_output_consistency(np.array([[0.5, 0.5]]), tee_output, quantization_scale=13)


def test_ezkl_backend_fails_at_construction_when_ezkl_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing() -> None:
        raise ProvingBackendUnavailableError("ezkl is not installed")

    monkeypatch.setattr(ezkl_backend, "load_ezkl", missing)

    with pytest.raises(ProvingBackendUnavailableError):
        ZkMLProver(SimpleNamespace(), backend="ezkl")  # type: ignore[arg-type]


# --- the prover -------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_prover_makes_a_proof_bound_to_the_tee_output(
    bundle: ezkl_backend.ProvingBundle, model_bytes: bytes, model_hash: str, store: Path
) -> None:
    registry = await registry_with(model_bytes, model_hash)
    input_bytes = encode_tensor(X)
    receipt = receipt_for(model_hash, input_bytes, run_model(model_bytes, X))
    prover = ZkMLProver(registry, proving_key_store=str(store), backend="ezkl")

    artifact = await prover.generate_proof(receipt, input_bytes)

    assert artifact.backend == "ezkl"
    assert artifact.public_inputs["output_hash"] == receipt.output_hash
    assert artifact.public_inputs["zk_instances_hash"] == ezkl_backend.instances_hash(artifact.proof_bytes)
    assert prover.verify_proof(artifact)


@pytest.mark.asyncio
async def test_prover_refuses_to_prove_without_the_input_the_tee_ran_on(
    model_bytes: bytes, model_hash: str, store: Path
) -> None:
    registry = await registry_with(model_bytes, model_hash)
    input_bytes = encode_tensor(X)
    receipt = receipt_for(model_hash, input_bytes, run_model(model_bytes, X))
    prover = ZkMLProver(registry, proving_key_store=str(store), backend="ezkl")

    with pytest.raises(ValueError, match="needs the input"):
        await prover.generate_proof(receipt)
    with pytest.raises(ValueError, match="does not match"):
        await prover.generate_proof(receipt, encode_tensor(X + 1.0))


@pytest.mark.asyncio
async def test_prover_refuses_a_tee_output_the_circuit_does_not_reproduce(
    bundle: ezkl_backend.ProvingBundle, model_bytes: bytes, model_hash: str, store: Path
) -> None:
    registry = await registry_with(model_bytes, model_hash)
    input_bytes = encode_tensor(X)
    wrong_output = run_model(model_bytes, X) + 0.5
    receipt = receipt_for(model_hash, input_bytes, wrong_output)
    prover = ZkMLProver(registry, proving_key_store=str(store), backend="ezkl")

    with pytest.raises(ProofInconsistentError):
        await prover.generate_proof(receipt, input_bytes)


@pytest.mark.asyncio
async def test_pipeline_drops_a_task_whose_model_has_no_bundle(
    model_bytes: bytes, model_hash: str, tmp_path: Path
) -> None:
    registry = await registry_with(model_bytes, model_hash)
    prover = ZkMLProver(registry, proving_key_store=str(tmp_path / "empty"), backend="ezkl")
    tee = TEERuntime("sgx")
    tee.initialize()
    pipeline = Pipeline(SimpleNamespace(), None, tee, prover, None)
    input_bytes = encode_tensor(X)
    receipt = receipt_for(model_hash, input_bytes, run_model(model_bytes, X))

    with pytest.raises(TaskDropped) as dropped:
        await pipeline._run_proof(InferenceRequest(model_id=model_hash, input_data=input_bytes), receipt, "task", "execution")

    assert dropped.value.outcome == OUTCOME_DROPPED_PROOF_KEYS_MISSING


# --- the verifier -----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_verifier_checks_an_ezkl_proof_against_the_stored_bundle(
    bundle: ezkl_backend.ProvingBundle, model_bytes: bytes, model_hash: str, store: Path
) -> None:
    registry = await registry_with(model_bytes, model_hash)
    input_bytes = encode_tensor(X)
    receipt = receipt_for(model_hash, input_bytes, run_model(model_bytes, X))
    prover = ZkMLProver(registry, proving_key_store=str(store), backend="ezkl")
    artifact = await prover.generate_proof(receipt, input_bytes)
    wire = serialize_proof_artifact(artifact)
    verifier = ConcreteZkMLVerifier(zkml_store=store)

    assert await verifier.verify(wire, artifact.public_inputs) == (True, "verified")
    assert (await verifier.verify(wire, {**artifact.public_inputs, "model_hash": "ab" * 32}))[0] is False
    assert (await verifier.verify(wire, {**artifact.public_inputs, "model_hash": "../escape"}))[0] is False
    assert (await verifier.verify(wire, {**artifact.public_inputs, "zk_instances_hash": "00" * 32}))[0] is False
