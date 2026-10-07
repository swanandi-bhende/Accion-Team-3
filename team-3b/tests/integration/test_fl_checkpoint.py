"""Wave 3 checkpoint tests: Property 11 round-trip and crash durability.

Step 15.5 closes the loop end-to-end: train with the orchestrator -> store via
the Model Registry -> resolve the CID -> run inference on the FL-produced model
through ``TEERuntime.execute_inference`` (Step 4). Each piece is validated in
isolation elsewhere; this test proves they compose.

Step 15.7 exercises the Step 14.5 incremental-persistence decision under a real
failure: a *separate process* running ``run_training`` is SIGKILLed mid-run and
the provenance for the rounds that finished must still be in Redis, even though
``run_training`` never returned. That requires a real Redis (a different process
must be able to read what this one wrote), so the test skips when none is
available, matching ``tests/integration/test_wave2_real_redis.py``.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

import fakeredis.aioredis as fakeredis
import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from common.exceptions import IntegrityCheckFailedError
from common.interfaces import StorageBackend
from common.model_registry import ModelRegistry
from common.models import FLTaskConfig
from federated_learning.orchestrator import FederatedLearningOrchestrator
from federated_learning.simulated_client import make_simulated_clients
from inference.tee_runtime import TEERuntime

ROOT = Path(__file__).resolve().parents[2]
PYTHON = sys.executable
HARNESS = ROOT / "scripts" / "fl_crash_harness.py"
REDIS_URL = os.environ.get("FL_REDIS_URL", "redis://localhost:6379")
SEALED_KEY = b"mock_sealed_key_0123456789abcdef"


class InMemoryStorage(StorageBackend):
    def __init__(self):
        self.store: dict[str, bytes] = {}

    async def upload(self, data: bytes) -> str:
        cid = "Qm" + hashlib.sha256(data).hexdigest()
        self.store[cid] = data
        return cid

    async def retrieve(self, cid: str) -> bytes:
        return self.store[cid]


@pytest.mark.asyncio
async def test_property_11_trained_model_runs_through_the_tee_runtime():
    """Step 15.5: train -> store -> resolve -> execute on the FL-produced model."""
    registry = ModelRegistry(fakeredis.FakeRedis(decode_responses=False), InMemoryStorage())
    orchestrator = FederatedLearningOrchestrator(
        registry,
        registry.redis,
        make_simulated_clients(3, seed=5),
        model_id="fl-tee",
        architecture="mlp",
    )
    result = await orchestrator.run_training(
        FLTaskConfig(task_id="p11-tee", num_clients=3, num_rounds=3)
    )

    # The registry resolves the trained model's CID back to the exact binary.
    entry = await registry.get("fl-tee")
    assert entry is not None
    assert entry.cid == result.model_cid
    model_binary = await registry.storage.retrieve(entry.cid)
    assert hashlib.sha256(model_binary).hexdigest() == entry.hash == result.model_hash

    # ...and that binary is accepted by the TEE runtime and produces output.
    tee = TEERuntime("sgx")
    tee.initialize()
    try:
        output = await tee.execute_inference(
            model_binary=model_binary,
            model_hash=entry.hash,
            input_data=b"fl-toy-input",
            sealed_key=SEALED_KEY,
        )
    finally:
        tee.shutdown()
    assert output == b"simulated_output_for: fl-toy-input"

    # A blob whose plaintext does not hash to the expected model hash is refused.
    tee = TEERuntime("sgx")
    tee.initialize()
    try:
        nonce = os.urandom(12)
        sealed_blob = nonce + AESGCM(SEALED_KEY).encrypt(nonce, b"some-other-model", None)
        with pytest.raises(IntegrityCheckFailedError):
            await tee.execute_inference(
                model_binary=sealed_blob,
                model_hash=hashlib.sha256(b"expected-model").hexdigest(),
                input_data=b"x",
                sealed_key=SEALED_KEY,
            )
    finally:
        tee.shutdown()


def _redis_or_skip():
    try:
        import redis as sync_redis
    except ImportError:  # pragma: no cover - redis is a runtime dependency
        pytest.skip("redis package is not installed")
    client = sync_redis.Redis.from_url(REDIS_URL, decode_responses=True)
    try:
        client.ping()
    except Exception:
        pytest.skip("Redis service is not available")
    return client


def test_provenance_survives_a_real_mid_run_process_kill():
    """Step 15.7: rounds that completed before a crash stay durably in Redis."""
    client = _redis_or_skip()
    task_id = f"crash-{uuid.uuid4().hex[:8]}"
    model_id = f"crash-model-{uuid.uuid4().hex[:8]}"
    key = f"fl:provenance:{task_id}"

    env = dict(
        os.environ,
        REDIS_URL=REDIS_URL,
        FL_TASK_ID=task_id,
        FL_MODEL_ID=model_id,
        FL_NUM_CLIENTS="3",
        FL_NUM_ROUNDS="5",
        FL_ROUND_DELAY="0.4",
        PYTHONPATH=str(ROOT),
    )
    process = subprocess.Popen([PYTHON, str(HARNESS)], cwd=ROOT, env=env)
    try:
        deadline = time.time() + 30
        records: list[str] = []
        while time.time() < deadline:
            records = client.lrange(key, 0, -1)
            if len(records) >= 2:
                break
            time.sleep(0.05)
        assert len(records) >= 2, "harness never persisted any provenance"

        # Crash the run hard, in the middle of its rounds.
        process.kill()
        process.wait(timeout=10)
        assert process.returncode != 0, "the harness was supposed to die mid-run"

        survived = [json.loads(entry) for entry in client.lrange(key, 0, -1)]
        assert len(survived) >= 2, "provenance did not survive the crash"
        assert [record["round_number"] for record in survived] == list(
            range(1, len(survived) + 1)
        )
        assert all(record["participating_clients"] == 3 for record in survived)
        # The run never reached its final round, so no model was ever registered.
        assert client.get(f"model:{model_id}:latest") is None
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)
        client.delete(key)
        client.close()
