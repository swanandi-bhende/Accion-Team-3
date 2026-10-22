"""Build the EZKL proving bundle for every seeded model.

Proving keys are generated ahead of time, so a task never waits for key generation.
Each bundle is stored under ``<store>/<model_hash>/``, the directory the prover and the
verification API load from. Bundles that already exist are reused.

Needs the ``ezkl`` package (``pip install -r requirements/zkml.txt``) and an ezkl wheel
for this platform. Run from the repository root:

    venv/bin/python -m scripts.zkml.build_proving_bundles --store data/proving_keys
"""

import argparse
import hashlib
import json
import logging
from pathlib import Path

from common.config import get_settings
from proof_generator import ezkl_backend
from scripts.ops.seed_model_registry import MODELS

logger = logging.getLogger(__name__)


def build_all(store: Path) -> list[dict[str, object]]:
    """Build (or reuse) a bundle per seeded model and return a summary per model."""
    summaries: list[dict[str, object]] = []
    for model_id, (architecture, binary) in MODELS.items():
        model_hash = hashlib.sha256(binary).hexdigest()
        bundle = ezkl_backend.build_bundle(store, binary, model_hash, architecture)
        summaries.append(
            {
                "model_id": model_id,
                "model_hash": model_hash,
                "architecture": architecture,
                "logrows": bundle.logrows,
                "num_inner_cols": bundle.num_inner_cols,
                "quantization_scale": bundle.quantization_scale,
                "pk_bytes": bundle.pk_path.stat().st_size,
                "directory": str(bundle.directory),
            }
        )
    return summaries


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--store",
        type=Path,
        default=Path(get_settings().zkml_proving_key_store),
        help="directory the bundles are written to (default: ZKML_PROVING_KEY_STORE)",
    )
    args = parser.parse_args()
    for summary in build_all(args.store):
        print(json.dumps(summary, sort_keys=True), flush=True)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
