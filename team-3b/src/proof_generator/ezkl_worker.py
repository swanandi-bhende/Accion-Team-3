"""EZKL operations, run in a child process.

The ezkl package is native code with its own thread pool. Used in the service process,
it has been seen to abort the interpreter at exit (a libc++ mutex error), which would take
the pipeline or the verification API down on shutdown. Each operation therefore runs here,
in a child process. The child writes its result and then calls ``os._exit``, so the native
teardown never runs. Cancelling a proof kills the child, as the inference sandbox does.

Protocol. The parent writes one JSON request to stdin, ``{"op": name, "payload": {...}}``.
The child writes one JSON response as the last line of stdout:
``{"ok": true, "result": ...}``, or ``{"ok": false, "error_type": ..., "error": ...}``.
Anything ezkl prints goes to stderr, so it cannot corrupt the response.
"""

import importlib
import json
import os
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from common.exceptions import ProvingKeysUnavailableError
from proof_generator.ezkl_backend import (
    COMPILED_FILE,
    LOGROWS_SEARCH_DEPTH,
    PK_FILE,
    SETTINGS_FILE,
    SRS_FILE,
    VK_FILE,
)


def _ezkl() -> Any:
    return importlib.import_module("ezkl")


def _try_candidate(
    ezkl: Any,
    work: Path,
    model: Path,
    calibration: Path,
    base_settings: dict[str, Any],
    logrows: int,
    inner_cols: int,
) -> str | None:
    """Build one circuit size and check it proves and verifies. Returns its directory name."""
    name = f"k{logrows}_inner{inner_cols}"
    directory = work / name
    directory.mkdir()
    settings_path = directory / SETTINGS_FILE
    run_args = {**base_settings["run_args"], "logrows": logrows, "num_inner_cols": inner_cols}
    settings_path.write_text(json.dumps({**base_settings, "run_args": run_args}))
    compiled, srs, vk, pk = (directory / file for file in (COMPILED_FILE, SRS_FILE, VK_FILE, PK_FILE))
    witness, proof = directory / "witness.json", directory / "proof.json"
    try:
        ezkl.compile_circuit(str(model), str(compiled), str(settings_path))
        ezkl.gen_srs(str(srs), logrows)
        ezkl.setup(str(compiled), str(vk), str(pk), str(srs))
        ezkl.gen_witness(str(calibration), str(compiled), str(witness), str(vk), str(srs))
        ezkl.prove(str(witness), str(compiled), str(pk), str(proof), str(srs))
        if not ezkl.verify(str(proof), str(settings_path), str(vk), str(srs)):
            return None
    except BaseException as error:  # EZKL reports some failures as a Rust panic
        if isinstance(error, (KeyboardInterrupt, SystemExit)):
            raise
        sys.stderr.write(f"EZKL_CANDIDATE_REJECTED logrows={logrows} inner_cols={inner_cols} error={error}\n")
        return None
    return name


def op_ping(_payload: dict[str, Any]) -> dict[str, Any]:
    ezkl = _ezkl()
    return {"version": getattr(ezkl, "__version__", "unknown")}


def op_build(payload: dict[str, Any]) -> dict[str, Any]:
    """Calibrate the model, then keep the verifying circuit size with the smallest proving key."""
    ezkl = _ezkl()
    model = Path(payload["model_path"])
    calibration = Path(payload["calibration_path"])
    base_settings = Path(payload["base_settings_path"])
    work = Path(payload["work_dir"])

    ezkl.gen_settings(str(model), str(base_settings))
    ezkl.calibrate_settings(str(calibration), str(model), str(base_settings), "resources")
    settings = json.loads(base_settings.read_text())
    calibrated = settings["run_args"]
    calibrated_logrows = int(calibrated["logrows"])
    calibrated_inner = int(calibrated["num_inner_cols"])

    candidates = [(calibrated_logrows, calibrated_inner)]
    candidates += [(k, 1) for k in range(calibrated_logrows - 1, calibrated_logrows - 1 - LOGROWS_SEARCH_DEPTH, -1)]
    verified: list[tuple[int, int, int, str]] = []
    for logrows, inner_cols in candidates:
        if logrows < 1:
            continue
        name = _try_candidate(ezkl, work, model, calibration, settings, logrows, inner_cols)
        if name is not None:
            verified.append(((work / name / PK_FILE).stat().st_size, logrows, inner_cols, name))
    if not verified:
        raise ProvingKeysUnavailableError("no circuit size proved the model")

    _, logrows, inner_cols, best = min(verified)
    return {
        "best": best,
        "logrows": logrows,
        "num_inner_cols": inner_cols,
        "calibrated_logrows": calibrated_logrows,
        "candidates_verified": sorted([lr, inn] for _, lr, inn, _ in verified),
        "quantization_scale": min(int(calibrated["input_scale"]), int(calibrated["param_scale"])),
    }


def op_prove(payload: dict[str, Any]) -> dict[str, Any]:
    ezkl = _ezkl()
    directory = Path(payload["directory"])
    with tempfile.TemporaryDirectory(prefix="ezkl-prove-") as scratch:
        scratch_path = Path(scratch)
        input_path = scratch_path / "input.json"
        input_path.write_text(json.dumps({"input_data": payload["input_data"]}))
        witness, proof = scratch_path / "witness.json", scratch_path / "proof.json"
        ezkl.gen_witness(
            str(input_path),
            str(directory / COMPILED_FILE),
            str(witness),
            str(directory / VK_FILE),
            str(directory / SRS_FILE),
        )
        ezkl.prove(
            str(witness),
            str(directory / COMPILED_FILE),
            str(directory / PK_FILE),
            str(proof),
            str(directory / SRS_FILE),
        )
        proof_text = proof.read_text()
    rescaled = json.loads(proof_text)["pretty_public_inputs"]["rescaled_outputs"]
    return {"proof": proof_text, "outputs": rescaled}


def op_verify(payload: dict[str, Any]) -> dict[str, Any]:
    ezkl = _ezkl()
    directory = Path(payload["directory"])
    with tempfile.TemporaryDirectory(prefix="ezkl-verify-") as scratch:
        proof_path = Path(scratch) / "proof.json"
        proof_path.write_text(payload["proof"])
        try:
            verified = bool(
                ezkl.verify(
                    str(proof_path),
                    str(directory / SETTINGS_FILE),
                    str(directory / VK_FILE),
                    str(directory / SRS_FILE),
                )
            )
        except Exception:  # a malformed proof is a failed check, not a fault
            verified = False
    return {"verified": verified}


OPERATIONS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "ping": op_ping,
    "build": op_build,
    "prove": op_prove,
    "verify": op_verify,
}


def main() -> None:
    request = json.loads(sys.stdin.read())
    protocol_out = sys.stdout
    sys.stdout = sys.stderr  # whatever ezkl prints must not reach the response line
    try:
        response: dict[str, Any] = {"ok": True, "result": OPERATIONS[request["op"]](request["payload"])}
    except BaseException as error:  # EZKL reports some failures as a Rust panic
        response = {"ok": False, "error_type": type(error).__name__, "error": str(error)[:500]}
    protocol_out.write(json.dumps(response) + "\n")
    protocol_out.flush()
    sys.stderr.flush()
    # Skip interpreter teardown: that is where ezkl's native threads abort the process.
    os._exit(0)


if __name__ == "__main__":
    main()
