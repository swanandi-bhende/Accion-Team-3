"""The EZKL proving backend: bundle construction, proving and verification.

A bundle is everything a model's proofs need: the calibrated settings, the compiled
circuit, the verification key, the proving key and the SRS. Bundles are built once per
model, ahead of time, and stored under ``<store>/<model_hash>/``. Proving and verifying
only load them, so no request waits for key generation.

Building a bundle tries the circuit size EZKL's resource calibration picks, and a few
smaller ones. It keeps the candidate with the smallest proving key that proves and
verifies a calibration input. On the spike's linear fixture that is 12 rows with one
inner column: a 24 MB key, against 553 MB at the spike's default size.

Every EZKL operation runs in a child process (``src/proof_generator/ezkl_worker.py``). This
module holds the bundle layout and the orchestration, and imports nothing native.
"""

import asyncio
import hashlib
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from common.child_process import child_env
from common.constants import EZKL_OUTPUT_TOLERANCE_STEPS
from common.exceptions import (
    ProofGenerationError,
    ProofInconsistentError,
    ProvingBackendUnavailableError,
    ProvingKeysUnavailableError,
)

logger = logging.getLogger(__name__)

BUNDLE_FILE = "bundle.json"
SETTINGS_FILE = "settings.json"
COMPILED_FILE = "compiled.onnx"
VK_FILE = "vk.key"
PK_FILE = "pk.key"
SRS_FILE = "kzg.srs"
MODEL_FILE = "model.onnx"
CALIBRATION_SEED = 0
# Circuit sizes tried below the calibrated one. Smaller circuits can prove the same model.
LOGROWS_SEARCH_DEPTH = 3

WORKER_MODULE = "proof_generator.ezkl_worker"
REPO_ROOT = Path(__file__).resolve().parents[2]
PING_TIMEOUT_S = 120.0
PROVE_TIMEOUT_S = 120.0
VERIFY_TIMEOUT_S = 120.0
BUILD_TIMEOUT_S = 1800.0


@dataclass(frozen=True)
class ProvingBundle:
    """One model's proving material, as stored on disk."""

    directory: Path
    model_hash: str
    architecture: str
    logrows: int
    num_inner_cols: int
    # The circuit's fixed-point step: the smaller of the input and parameter scales. Its
    # output differs from the float output by a few of these steps.
    quantization_scale: int

    @property
    def settings_path(self) -> Path:
        return self.directory / SETTINGS_FILE

    @property
    def compiled_path(self) -> Path:
        return self.directory / COMPILED_FILE

    @property
    def vk_path(self) -> Path:
        return self.directory / VK_FILE

    @property
    def pk_path(self) -> Path:
        return self.directory / PK_FILE

    @property
    def srs_path(self) -> Path:
        return self.directory / SRS_FILE


@dataclass(frozen=True)
class EzklProof:
    """A proof and the circuit's own output, rescaled to the model's float range."""

    proof_bytes: bytes
    outputs: np.ndarray


# --- running EZKL in a child process ----------------------------------------------------


def _raise_worker_error(error_type: str, message: str) -> None:
    if error_type in ("ModuleNotFoundError", "ImportError"):
        raise ProvingBackendUnavailableError(
            "the ezkl package is not installed: pip install -r requirements/zkml.txt"
        )
    if error_type == "ProvingKeysUnavailableError":
        raise ProvingKeysUnavailableError(message)
    raise ProofGenerationError(f"{error_type}: {message}")


def _parse_response(stdout: bytes, stderr: bytes) -> Any:
    lines = [line for line in stdout.decode(errors="replace").splitlines() if line.strip()]
    if not lines:
        raise ProofGenerationError(f"the ezkl worker returned no result: {stderr.decode(errors='replace')[-400:]}")
    try:
        response = json.loads(lines[-1])
    except json.JSONDecodeError as error:
        raise ProofGenerationError("the ezkl worker returned a malformed result") from error
    if response.get("ok"):
        return response["result"]
    _raise_worker_error(str(response.get("error_type", "")), str(response.get("error", "")))
    raise AssertionError("unreachable")


def _request(op: str, payload: dict[str, Any]) -> bytes:
    return json.dumps({"op": op, "payload": payload}).encode()


def _run_worker(op: str, payload: dict[str, Any], timeout_s: float) -> Any:
    """Run one EZKL operation in a child process, and wait for its result."""
    try:
        completed = subprocess.run(
            [sys.executable, "-m", WORKER_MODULE],
            input=_request(op, payload),
            capture_output=True,
            cwd=REPO_ROOT,
            env=child_env(),
            timeout=timeout_s,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise ProofGenerationError(f"ezkl {op} exceeded {timeout_s:.0f}s") from error
    return _parse_response(completed.stdout, completed.stderr)


async def _run_worker_async(op: str, payload: dict[str, Any]) -> Any:
    """Run one EZKL operation in a child process. Cancelling the caller kills the child."""
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        WORKER_MODULE,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=REPO_ROOT,
        env=child_env(),
    )
    try:
        stdout, stderr = await process.communicate(_request(op, payload))
    except asyncio.CancelledError:
        if process.returncode is None:
            process.kill()
        await process.wait()
        raise
    return _parse_response(stdout, stderr)


def load_ezkl() -> str:
    """Check that the ezkl toolchain imports, and return its version.

    Raises ``ProvingBackendUnavailableError`` when the package is missing, so a service
    configured for EZKL refuses to start instead of failing on the first task.
    """
    return str(_run_worker("ping", {}, PING_TIMEOUT_S)["version"])


# --- bundles -----------------------------------------------------------------------------


def load_bundle(store: Path, model_hash: str) -> ProvingBundle:
    """Load a stored bundle, or raise ``ProvingKeysUnavailableError`` if there is none."""
    directory = store / model_hash
    metadata_path = directory / BUNDLE_FILE
    if not metadata_path.exists():
        raise ProvingKeysUnavailableError(f"no proving bundle for model hash {model_hash}")
    metadata = json.loads(metadata_path.read_text())
    if "quantization_scale" not in metadata:
        raise ProvingKeysUnavailableError(
            f"the bundle at {directory} predates the current format; delete that directory and rebuild it"
        )
    return ProvingBundle(
        directory=directory,
        model_hash=model_hash,
        architecture=metadata["architecture"],
        logrows=int(metadata["logrows"]),
        num_inner_cols=int(metadata["num_inner_cols"]),
        quantization_scale=int(metadata["quantization_scale"]),
    )


def instances_hash(proof_bytes: bytes) -> str:
    """SHA-256 of a proof's public instances, a canonical form of the claim it makes."""
    instances = json.loads(proof_bytes)["instances"]
    canonical = json.dumps(instances, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(canonical).hexdigest()


def _calibration_input(model_bytes: bytes) -> list[list[float]]:
    """A deterministic input shaped like the model's single 2-D float input."""
    import onnx

    graph = onnx.load_from_string(model_bytes).graph
    # A dynamic batch dimension reads as 0 here; the circuit is proved for one row.
    dims = [dim.dim_value for dim in graph.input[0].type.tensor_type.shape.dim]
    if len(dims) != 2 or dims[0] not in (0, 1) or dims[1] <= 0:
        raise ProvingKeysUnavailableError(f"the EZKL backend needs a (batch, features) input, got {dims}")
    rng = np.random.default_rng(CALIBRATION_SEED)
    return [rng.uniform(-1.0, 1.0, size=dims[1]).tolist()]


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value))


def build_bundle(store: Path, model_bytes: bytes, model_hash: str, architecture: str) -> ProvingBundle:
    """Build and store the proving bundle for one model. Existing bundles are reused.

    The bundle is built in a staging directory and renamed into place, so a failed or
    interrupted build never leaves a partial bundle that a prover could load.
    """
    store.mkdir(parents=True, exist_ok=True)
    target = store / model_hash
    if (target / BUNDLE_FILE).exists():
        return load_bundle(store, model_hash)

    staging = Path(tempfile.mkdtemp(prefix=f".{model_hash}.", dir=store))
    try:
        model = staging / MODEL_FILE
        model.write_bytes(model_bytes)
        calibration = staging / "calibration.json"
        _write_json(calibration, {"input_data": _calibration_input(model_bytes)})
        base_settings = staging / "base_settings.json"
        work = staging / "candidates"
        work.mkdir()

        result = _run_worker(
            "build",
            {
                "model_path": str(model),
                "calibration_path": str(calibration),
                "base_settings_path": str(base_settings),
                "work_dir": str(work),
            },
            BUILD_TIMEOUT_S,
        )
        best = work / str(result["best"])
        for name in (COMPILED_FILE, SRS_FILE, VK_FILE, PK_FILE, SETTINGS_FILE):
            shutil.copy2(best / name, staging / name)
        _write_json(
            staging / BUNDLE_FILE,
            {
                "model_hash": model_hash,
                "architecture": architecture,
                "logrows": result["logrows"],
                "num_inner_cols": result["num_inner_cols"],
                "quantization_scale": result["quantization_scale"],
                "calibrated_logrows": result["calibrated_logrows"],
                "candidates_verified": result["candidates_verified"],
            },
        )
        shutil.rmtree(work)
        model.unlink()
        calibration.unlink()
        base_settings.unlink()
        os.replace(staging, target)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return load_bundle(store, model_hash)


# --- proving and verifying ---------------------------------------------------------------


def _prove_payload(bundle: ProvingBundle, tensor: np.ndarray) -> dict[str, Any]:
    return {"directory": str(bundle.directory), "input_data": np.asarray(tensor, dtype=np.float64).tolist()}


def _proof_from(result: dict[str, Any]) -> EzklProof:
    return EzklProof(
        proof_bytes=str(result["proof"]).encode(),
        outputs=np.asarray(result["outputs"], dtype=np.float64),
    )


def prove(bundle: ProvingBundle, tensor: np.ndarray) -> EzklProof:
    """Prove the circuit on one input and return the proof with the circuit's output."""
    return _proof_from(_run_worker("prove", _prove_payload(bundle, tensor), PROVE_TIMEOUT_S))


async def prove_async(bundle: ProvingBundle, tensor: np.ndarray) -> EzklProof:
    """Like ``prove``, for the async pipeline. Cancelling it kills the proving process."""
    return _proof_from(await _run_worker_async("prove", _prove_payload(bundle, tensor)))


def verify(bundle: ProvingBundle, proof_bytes: bytes) -> bool:
    """Check a proof against the bundle's verification key. Malformed proofs fail, not raise."""
    try:
        result = _run_worker(
            "verify",
            {"directory": str(bundle.directory), "proof": proof_bytes.decode(errors="replace")},
            VERIFY_TIMEOUT_S,
        )
    except (ProofGenerationError, ProvingBackendUnavailableError):
        return False
    return bool(result["verified"])


def check_output_consistency(circuit_outputs: np.ndarray, tee_output: np.ndarray, quantization_scale: int) -> None:
    """Require the circuit's output to match the TEE's within the quantization tolerance.

    The circuit computes on fixed-point inputs and weights, so its output differs from the
    float output by a few steps of ``2**-quantization_scale``. A larger gap means the proof
    and the attested result describe different computations.
    """
    expected = np.asarray(tee_output, dtype=np.float64)
    if expected.shape != circuit_outputs.shape:
        raise ProofInconsistentError(f"output shape {circuit_outputs.shape} != TEE output shape {expected.shape}")
    tolerance = EZKL_OUTPUT_TOLERANCE_STEPS * 2.0 ** -quantization_scale
    gap = float(np.max(np.abs(circuit_outputs - expected)))
    if gap > tolerance:
        raise ProofInconsistentError(f"circuit output differs from TEE output by {gap:.3g} (tolerance {tolerance:.3g})")
