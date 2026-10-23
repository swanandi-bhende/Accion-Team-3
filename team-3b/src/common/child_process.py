"""Environment for child Python processes that run this project's modules.

The packages live under ``src/``, so a child started with ``python -m <package>`` cannot import
them unless ``src/`` is on its path. ``child_env`` adds it, so the child works from any directory
and without an editable install.
"""

import os
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[1]


def child_env(**extra: str) -> dict[str, str]:
    """The current environment with ``src/`` first on PYTHONPATH, plus ``extra`` variables."""
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(SRC_DIR), env.get("PYTHONPATH")]))
    env.update(extra)
    return env
