#!/usr/bin/env python
"""End-to-end smoke test for the federated-learning runner.

Runs the *documented* entrypoint (``python -m federated_learning.runner``) as a
subprocess and checks the artifacts it claims to produce, rather than importing
the orchestrator in-process where a miswired entrypoint would go unnoticed.

Two dependencies are satisfied locally so the check needs no Docker:

* **Redis** -- a private ``redis-server`` instance on an ephemeral port, so the
  developer's own Redis (6379) and its data are never touched.
* **IPFS** -- the runner hardwires ``IPFSBackend``, so this starts an HTTP server
  implementing the two IPFS API calls the backend uses (``/api/v0/add`` and
  ``/api/v0/cat``). This exercises the real ``httpx`` path, multipart framing
  included. It is **not** a Kubo node: it verifies the API contract the backend
  depends on, not IPFS itself.

For each aggregation strategy it asserts the durable effects:

1. the command exits 0;
2. ``fl:provenance:<task_id>`` holds exactly ``num_rounds`` records, each with
   the documented ``RoundProvenance`` fields and the real participation count;
3. ``model:<model_id>:latest`` is registered with the configured architecture;
4. the stored binary hashes to the registered hash *and* to the final round's
   ``resulting_model_hash`` -- the chain from last aggregation to stored model.

Usage::

    python scripts/fl/fl_e2e_smoke.py
"""

from __future__ import annotations

import hashlib
import http.server
import json
import os
import socket
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
PYTHON = str(ROOT / "venv" / "bin" / "python")
NUM_CLIENTS = 3
CASES = (("fedavg", 2), ("krum", 3))

_STORED_OBJECTS: dict[str, bytes] = {}


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port: int = probe.getsockname()[1]
    return port


def _multipart_file(body: bytes, content_type: str) -> bytes:
    """Extract the single uploaded file payload from a multipart/form-data body."""
    boundary = content_type.split("boundary=")[1].strip().encode()
    part = body.split(b"--" + boundary)[1]
    return part.split(b"\r\n\r\n", 1)[1].rsplit(b"\r\n", 1)[0]


class _IPFSAPI(http.server.BaseHTTPRequestHandler):
    """The subset of the IPFS HTTP API that :class:`IPFSBackend` actually calls."""

    protocol_version = "HTTP/1.1"

    def log_message(self, *args: Any) -> None:  # keep the smoke output readable
        pass

    def do_POST(self) -> None:  # noqa: N802 - stdlib naming
        raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        if self.path.startswith("/api/v0/add"):
            cid = "Qm" + hashlib.sha256(raw).hexdigest()[:44]
            _STORED_OBJECTS[cid] = _multipart_file(raw, self.headers["Content-Type"])
            payload = json.dumps({"Name": "model.bin", "Hash": cid, "Size": "0"}).encode()
        elif self.path.startswith("/api/v0/cat"):
            stored = _STORED_OBJECTS.get(self.path.split("arg=")[1])
            if stored is None:
                self.send_error(500)
                return
            payload = stored
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def _run_runner(strategy: str, task_id: str, model_id: str, rounds: int, base: dict) -> bool:
    env = dict(
        base,
        FL_TASK_ID=task_id,
        FL_NUM_CLIENTS=str(NUM_CLIENTS),
        FL_NUM_ROUNDS=str(rounds),
        FL_AGGREGATION_STRATEGY=strategy,
        FL_MODEL_ID=model_id,
        FL_MODEL_ARCHITECTURE="mlp",
        FL_ROUND_TIMEOUT="60",
    )
    completed = subprocess.run(
        [PYTHON, "-m", "federated_learning.runner"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    print(f"--- {strategy}: exit={completed.returncode}")
    print(f"    stdout: {completed.stdout.strip() or '(none)'}")
    if completed.returncode != 0:
        print(f"    stderr: {completed.stderr.strip()[-1500:]}")
    return completed.returncode == 0


def _check_artifacts(redis_client: Any, strategy: str, task_id: str, model_id: str, rounds: int) -> bool:
    records = redis_client.lrange(f"fl:provenance:{task_id}", 0, -1)
    print(f"    fl:provenance:{task_id} -> {len(records)} records")
    if len(records) != rounds:
        print(f"    !! expected {rounds} provenance records")
        return False

    parsed = [json.loads(record) for record in records]
    expected_fields = {
        "round_number",
        "participating_clients",
        "aggregation_strategy",
        "round_duration",
        "resulting_model_hash",
    }
    for record in parsed:
        if set(record) != expected_fields:
            print(f"    !! unexpected provenance fields: {sorted(record)}")
            return False
        if record["aggregation_strategy"] != strategy or record["participating_clients"] != NUM_CLIENTS:
            print(f"    !! wrong participation/strategy: {record}")
            return False
    print(f"    rounds: {[(r['round_number'], r['participating_clients']) for r in parsed]}")

    latest_key = redis_client.get(f"model:{model_id}:latest")
    entry = redis_client.hgetall(latest_key)
    print(f"    registry {latest_key} -> {entry}")
    if not entry or entry.get("hash") != parsed[-1]["resulting_model_hash"]:
        print("    !! registered model hash differs from the final round's hash")
        return False
    if entry.get("architecture") != "mlp":
        print("    !! architecture was not persisted")
        return False

    stored = _STORED_OBJECTS.get(entry.get("cid", ""))
    if stored is None or hashlib.sha256(stored).hexdigest() != entry["hash"]:
        print("    !! stored binary does not hash to the registered hash")
        return False
    print("    stored binary sha256 matches the registered + final-round hash")
    return True


def main() -> int:
    try:
        import redis
    except ImportError:
        print("redis package is required (pip install -r requirements/base.txt)")
        return 2

    redis_port = _free_port()
    ipfs_port = _free_port()
    workdir = tempfile.mkdtemp(prefix="fl-e2e-")
    redis_process = subprocess.Popen(
        [
            "redis-server",
            "--port",
            str(redis_port),
            "--save",
            "",
            "--appendonly",
            "no",
            "--dir",
            workdir,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    ipfs_server = socketserver.ThreadingTCPServer(("127.0.0.1", ipfs_port), _IPFSAPI)
    ipfs_server.daemon_threads = True
    threading.Thread(target=ipfs_server.serve_forever, daemon=True).start()

    ok = True
    try:
        client = redis.Redis(port=redis_port, decode_responses=True)
        for _ in range(50):
            try:
                client.ping()
                break
            except redis.exceptions.ConnectionError:
                time.sleep(0.1)
        else:
            print("isolated redis-server never became reachable")
            return 2

        print(f"isolated redis on {redis_port}, IPFS-API double on {ipfs_port}\n")
        base = dict(
            os.environ,
            REDIS_URL=f"redis://127.0.0.1:{redis_port}",
            MODEL_STORAGE_URL=f"http://127.0.0.1:{ipfs_port}",
        )
        for strategy, rounds in CASES:
            task_id, model_id = f"e2e-{strategy}", f"e2e-model-{strategy}"
            if not _run_runner(strategy, task_id, model_id, rounds, base):
                ok = False
                continue
            if not _check_artifacts(client, strategy, task_id, model_id, rounds):
                ok = False
            print()
    finally:
        ipfs_server.shutdown()
        redis_process.terminate()

    print("E2E:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
