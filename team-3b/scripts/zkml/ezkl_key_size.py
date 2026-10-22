"""Measure how the EZKL proving key and proof sizes depend on circuit size.

The proving key grows with the circuit's row count (2**logrows). EZKL's resource
calibration picks logrows for the calibrated scale, and that choice is not always the
smallest that still proves. This script calibrates, then forces smaller logrows and
inner-column counts. A candidate counts only if setup, prove and verify all succeed.

Run from the repository root:

    venv/bin/python scripts/zkml/ezkl_key_size.py data/zkml_spike/linear --out /tmp/ezkl-key-size

The model directory must hold ``tiny_linear.onnx`` and ``input.json`` (the spike's
fixture). Results are printed as JSON lines and written to ``results.json`` in the
output directory.
"""

import argparse
import importlib
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any


def measure(model_dir: Path, workdir: Path, logrows: int | None, inner_cols: int | None) -> dict[str, Any]:
    """Run one candidate end to end, starting from the calibrated settings."""
    # Forced sizes need per-candidate control, so this script drives ezkl directly rather
    # than through the bundle builder. It is a one-off tool, so it ends with os._exit.
    ezkl: Any = importlib.import_module("ezkl")
    workdir.mkdir(parents=True, exist_ok=True)
    model = workdir / "model.onnx"
    input_path = workdir / "input.json"
    settings = workdir / "settings.json"
    compiled = workdir / "compiled.onnx"
    srs, vk, pk = workdir / "kzg.srs", workdir / "vk.key", workdir / "pk.key"
    witness, proof = workdir / "witness.json", workdir / "proof.json"
    shutil.copy(model_dir / "tiny_linear.onnx", model)
    shutil.copy(model_dir / "input.json", input_path)

    ezkl.gen_settings(str(model), str(settings))
    ezkl.calibrate_settings(str(input_path), str(model), str(settings), "resources")
    run_args = json.loads(settings.read_text())["run_args"]
    if logrows is not None:
        run_args["logrows"] = logrows
    if inner_cols is not None:
        run_args["num_inner_cols"] = inner_cols
    settings_data = json.loads(settings.read_text())
    settings_data["run_args"] = run_args
    settings.write_text(json.dumps(settings_data))

    row: dict[str, Any] = {
        "calibrated_logrows": None,
        "logrows": run_args["logrows"],
        "num_inner_cols": run_args["num_inner_cols"],
        "input_scale": run_args["input_scale"],
    }
    try:
        ezkl.compile_circuit(str(model), str(compiled), str(settings))
        k = json.loads(settings.read_text())["run_args"]["logrows"]
        ezkl.gen_srs(str(srs), k)
        ezkl.setup(str(compiled), str(vk), str(pk), str(srs))
        ezkl.gen_witness(str(input_path), str(compiled), str(witness), str(vk), str(srs))
        ezkl.prove(str(witness), str(compiled), str(pk), str(proof), str(srs))
        row["verified"] = bool(ezkl.verify(str(proof), str(settings), str(vk), str(srs)))
        row["pk_bytes"] = pk.stat().st_size
        row["vk_bytes"] = vk.stat().st_size
        row["proof_bytes"] = proof.stat().st_size
    except BaseException as error:  # EZKL reports some failures as a Rust panic
        row["verified"] = False
        row["error"] = f"{type(error).__name__}: {str(error)[:200]}"
    finally:
        if pk.exists():
            pk.unlink()
    return row


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("model_dir", type=Path, help="directory with tiny_linear.onnx and input.json")
    parser.add_argument("--out", type=Path, required=True, help="working directory for candidates")
    parser.add_argument("--logrows", type=int, nargs="*", default=[14, 13, 12, 11], help="circuit sizes to force")
    parser.add_argument("--inner-cols", type=int, nargs="*", default=[2, 1], help="num_inner_cols values to try")
    args = parser.parse_args()

    rows = [measure(args.model_dir, args.out / "calibrated", None, None)]
    rows[0]["calibrated_logrows"] = rows[0]["logrows"]
    print(json.dumps(rows[0]), flush=True)
    for inner in args.inner_cols:
        for k in args.logrows:
            row = measure(args.model_dir, args.out / f"k{k}_inner{inner}", k, inner)
            row["calibrated_logrows"] = rows[0]["calibrated_logrows"]
            rows.append(row)
            print(json.dumps(row), flush=True)

    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "results.json").write_text(json.dumps(rows, indent=2))


if __name__ == "__main__":
    main()
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0)  # skip the native teardown that aborts the process (see ezkl_worker.py)
