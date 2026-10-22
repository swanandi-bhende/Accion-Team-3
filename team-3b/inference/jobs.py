"""Asynchronous inference jobs: POST /jobs records work, GET /jobs/{id} reports it.

A job runs inference, the TEE attestation and the zkML proof for one input, and keeps the
result as a receipt. The POST returns as soon as the job is recorded, because the proof
stage can take much longer than an HTTP request should stay open.

Jobs are idempotent on ``(task_id, input_hash)``: the same task and input always map to one
job, however many times it is posted. The claim is an atomic Redis ``SET NX``, so two
identical posts racing each other still create one job. Job state lives in Redis, so a
restart resumes the jobs that had not finished.
"""

import asyncio
import base64
import hashlib
import json
import logging
import re
import time
import uuid
from collections.abc import Callable, Coroutine
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from common.exceptions import (
    IntegrityCheckFailedError,
    ModelExecutionError,
    ModelNotFoundError,
    ProofGenerationError,
    ProofInconsistentError,
    ProofNotSubmittableError,
    ProvingKeysUnavailableError,
    TEEUnavailableError,
    TimeoutExceededError,
    UnsupportedArchitectureError,
)
from common.models import AttestationReport, InferenceRequest, ProofArtifact
from common.tensors import decode_tensor
from inference.auth import Authorization
from verification.verifiers import serialize_attestation, serialize_proof_artifact

logger = logging.getLogger(__name__)

QUEUED = "queued"
RUNNING = "running"
COMPLETED = "completed"
FAILED = "failed"
FINISHED_STATUSES = frozenset({COMPLETED, FAILED})

JOB_KEY = "jobs:job:{}"
INPUT_KEY = "jobs:input:{}"
INDEX_KEY = "jobs:by_key:{}:{}"
PENDING_SET = "jobs:pending"
SETTLING_SET = "jobs:settling"

_TASK_ID = re.compile(r"^(0x)?[0-9a-fA-F]{64}$")
_SHA256_HEX = re.compile(r"^[0-9a-fA-F]{64}$")

# A job gets the inference budget (30 s, Requirement 1.1) plus the proof budget (120 s,
# Requirement 3.5). Clients poll GET /jobs/{id} for this long, then stop.
JOB_BUDGET_S = 30.0 + 120.0
POLL_AFTER_S = 5
# Settlement waits for chain confirmation, which can take minutes on Galileo. It runs after the
# receipt is delivered, so it has its own budget.
SETTLEMENT_BUDGET_S = 300.0


class InvalidJobError(ValueError):
    """The job request is well formed JSON but cannot be run: its hash or input is wrong."""


class JobConflictError(Exception):
    """The same (task_id, input_hash) was already posted for a different model."""


class JobCreate(BaseModel):
    """Body of POST /jobs."""

    task_id: str = Field(description="Team A task id: bytes32 hex, with or without 0x")
    model_id: str | None = Field(default=None, min_length=1, description="Model Registry id to run")
    model_cid: str | None = Field(default=None, min_length=1, description="CID of the model's current version")
    input_hash: str = Field(description="SHA-256 hex of the raw input bytes")
    input_data: bytes = Field(description="Base64 of the .npy input tensor")
    authorization: Authorization = Field(description="Signed challenge from POST /challenges")

    @model_validator(mode="after")
    def _exactly_one_model(self) -> "JobCreate":
        if (self.model_id is None) == (self.model_cid is None):
            raise ValueError("give exactly one of model_id or model_cid")
        return self

    @field_validator("task_id")
    @classmethod
    def _task_id_shape(cls, value: str) -> str:
        if not _TASK_ID.match(value):
            raise ValueError("task_id must be a 32-byte hex string")
        return "0x" + value.lower().removeprefix("0x")

    @field_validator("input_hash")
    @classmethod
    def _input_hash_shape(cls, value: str) -> str:
        if not _SHA256_HEX.match(value):
            raise ValueError("input_hash must be a SHA-256 hex digest")
        return value.lower()

    @field_validator("input_data", mode="before")
    @classmethod
    def _decode_base64(cls, value: object) -> object:
        if isinstance(value, str):
            try:
                return base64.b64decode(value, validate=True)
            except ValueError as error:
                raise ValueError("input_data must be base64") from error
        return value


def failure_code(error: BaseException) -> tuple[str, str]:
    """Map an exception from the job's stages to a stable error code and a detail."""
    mapping: list[tuple[type[BaseException], str]] = [
        (ModelNotFoundError, "model_not_found"),
        (TimeoutExceededError, "inference_timeout"),
        (IntegrityCheckFailedError, "integrity_failure"),
        (TEEUnavailableError, "tee_unavailable"),
        (ModelExecutionError, "model_execution_failed"),
        (ProvingKeysUnavailableError, "proof_keys_missing"),
        (ProofInconsistentError, "proof_inconsistent"),
        (UnsupportedArchitectureError, "unsupported_architecture"),
        (ProofGenerationError, "proof_failed"),
        (TimeoutError, "proof_timeout"),
        (ValueError, "invalid_input"),
    ]
    for error_type, code in mapping:
        if isinstance(error, error_type):
            return code, str(error)
    return "internal_error", type(error).__name__


class JobService:
    """Runs inference jobs in the background and keeps their state in Redis."""

    def __init__(
        self,
        redis_client: Any,
        engine: Any,
        tee: Any,
        prover: Any,
        max_concurrent: int = 4,
        timeout_s: float = JOB_BUDGET_S,
        submitter: Any = None,
        settlement_timeout_s: float = SETTLEMENT_BUDGET_S,
        clock: Callable[[], float] = time.time,
    ):
        self.redis = redis_client
        self.engine = engine
        self.tee = tee
        self.prover = prover
        self.submitter = submitter
        self.settlement_timeout_s = settlement_timeout_s
        self.timeout_s = timeout_s
        self.clock = clock
        self._slots = asyncio.Semaphore(max_concurrent)
        self._tasks: set[asyncio.Task[None]] = set()

    async def submit(self, task_id: str, input_hash: str, model_id: str, input_data: bytes) -> tuple[dict[str, Any], bool]:
        """Record a job, or return the job already recorded for this (task_id, input_hash).

        Returns ``(job, created)``. Raises ``InvalidJobError`` when the input does not match
        its hash or is not a tensor, and ``JobConflictError`` when the same key was posted
        for a different model.
        """
        if hashlib.sha256(input_data).hexdigest() != input_hash:
            raise InvalidJobError("input_hash does not match input_data")
        try:
            decode_tensor(input_data)
        except ValueError as error:
            raise InvalidJobError("input_data is not an .npy tensor") from error

        index = INDEX_KEY.format(task_id, input_hash)
        job_id = uuid.uuid4().hex
        if not await self.redis.set(index, job_id, nx=True):
            existing = await self._existing_for(index, model_id)
            return existing, False

        now = self.clock()
        job = {
            "job_id": job_id,
            "status": QUEUED,
            "task_id": task_id,
            "input_hash": input_hash,
            "model_id": model_id,
            "created_at": now,
            "updated_at": now,
            "deadline": now + self.timeout_s,
            "error": None,
            "receipt": None,
        }
        await self.redis.set(INPUT_KEY.format(job_id), input_data)
        await self._save(job)
        await self.redis.sadd(PENDING_SET, job_id)
        self._start(job_id)
        return job, True

    async def get(self, job_id: str) -> dict[str, Any] | None:
        return await self._load(job_id)

    async def resume(self) -> None:
        """Restart the unfinished jobs, for example after the service restarted."""
        for raw in await self.redis.smembers(PENDING_SET):
            job_id = raw.decode() if isinstance(raw, bytes) else raw
            job = await self._load(job_id)
            if job is None or job["status"] in FINISHED_STATUSES:
                await self.redis.srem(PENDING_SET, job_id)
                continue
            self._start(job_id)
        for raw in await self.redis.smembers(SETTLING_SET):
            self._track(self._settle_job(raw.decode() if isinstance(raw, bytes) else raw))

    async def shutdown(self) -> None:
        """Stop the running jobs. Their state stays unfinished, so the next start resumes them."""
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    def _start(self, job_id: str) -> None:
        self._track(self._run(job_id))

    def _track(self, work: Coroutine[Any, Any, None]) -> None:
        task = asyncio.create_task(work)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _existing_for(self, index: str, model_id: str) -> dict[str, Any]:
        raw_id = await self.redis.get(index)
        job_id = raw_id.decode() if isinstance(raw_id, bytes) else raw_id
        job = await self._load(job_id)
        if job is None:
            raise RuntimeError(f"job index {index} points at a job that was never recorded")
        if job["model_id"] != model_id:
            raise JobConflictError(
                f"task {job['task_id']} with this input is already a job for model {job['model_id']}"
            )
        return job

    async def _run(self, job_id: str) -> None:
        async with self._slots:
            job = await self._load(job_id)
            if job is None or job["status"] in FINISHED_STATUSES:
                return
            job["status"] = RUNNING
            await self._save(job)
            input_data = await self.redis.get(INPUT_KEY.format(job_id))
            try:
                async with asyncio.timeout(job["deadline"] - self.clock()) as budget:
                    receipt = await self._execute(job, input_data)
            except asyncio.CancelledError:
                # Shutdown: leave the job unfinished so the next start runs it again.
                raise
            except Exception as error:
                if budget.expired():
                    code, detail = "job_timeout", f"no result within the job's {self.timeout_s:.0f} s budget"
                else:
                    code, detail = failure_code(error)
                if code == "internal_error":
                    logger.exception("JOB_FAILED job_id=%s", job_id)
                else:
                    logger.warning("JOB_FAILED job_id=%s code=%s detail=%s", job_id, code, detail)
                job["status"] = FAILED
                job["error"] = {"code": code, "detail": detail}
            else:
                job["status"] = COMPLETED
                job["receipt"] = receipt
                logger.info("JOB_COMPLETED job_id=%s task_id=%s", job_id, job["task_id"])
            job["updated_at"] = self.clock()
            await self._save(job)
            await self.redis.delete(INPUT_KEY.format(job_id))
            await self.redis.srem(PENDING_SET, job_id)
            if job["status"] == COMPLETED and self.submitter is not None:
                await self._begin_settlement(job_id)

    async def _execute(self, job: dict[str, Any], input_data: bytes) -> dict[str, Any]:
        """Run the stages for one job and return its receipt as JSON-ready data."""
        request = InferenceRequest(model_id=job["model_id"], input_data=input_data, task_id=job["task_id"])
        execution = await self.engine.execute(request)
        attestation = self.tee.generate_attestation(input_data, execution.output)
        proof = await self.prover.generate_proof(execution, input_data)
        settlement = {"status": "pending"} if self.submitter is not None else {"status": "disabled"}
        return {
            "execution": execution.model_dump(mode="json"),
            "attestation": json.loads(serialize_attestation(attestation, self.tee.enclave.public_key_bytes)),
            "zkml_proof": json.loads(serialize_proof_artifact(proof)),
            "settlement": settlement,
        }

    async def _begin_settlement(self, job_id: str) -> None:
        await self.redis.sadd(SETTLING_SET, job_id)
        self._track(self._settle_job(job_id))

    async def _settle_job(self, job_id: str) -> None:
        """Settle a delivered receipt, then record the outcome on the job.

        It works from the stored receipt, not from in-memory objects, so a restart can finish it.
        Only a cancellation leaves the job pending; every other outcome is recorded.
        """
        job = await self._load(job_id)
        if job is None or job["receipt"] is None or job["receipt"]["settlement"]["status"] != "pending":
            await self.redis.srem(SETTLING_SET, job_id)
            return
        receipt = job["receipt"]
        try:
            async with asyncio.timeout(self.settlement_timeout_s):
                settlement = await self._settle(
                    job["task_id"],
                    attestation_from_receipt(receipt["attestation"]["report"]),
                    proof_from_receipt(receipt["zkml_proof"]),
                    receipt["execution"]["model_cid"],
                )
        except TimeoutError:
            settlement = {"status": "failed", "reason": f"settlement exceeded {self.settlement_timeout_s:.0f} s"}
        receipt["settlement"] = settlement
        job["updated_at"] = self.clock()
        await self._save(job)
        await self.redis.srem(SETTLING_SET, job_id)

    async def _settle(self, task_id: str, attestation: Any, proof: Any, model_cid: str) -> dict[str, Any]:
        """Submit the proof and the model CID on chain. A failure is recorded, not raised.

        The receipt is valid without settlement, so a refused or failed submission leaves the
        job completed, with the reason in ``settlement``.
        """
        if self.submitter is None:
            return {"status": "disabled"}
        try:
            await self.submitter.submit_proof(task_id, attestation, proof, model_cid)
            tx_hash = await self.submitter.flush()
        except ProofNotSubmittableError as error:
            return {"status": "not_submitted", "reason": str(error)}
        except Exception as error:
            logger.warning("JOB_SETTLEMENT_FAILED task_id=%s error=%s", task_id, error)
            return {"status": "failed", "reason": f"{type(error).__name__}: {error}"}
        if tx_hash is None:
            return {"status": "dead_lettered"}
        return {"status": "submitted", "tx_hash": tx_hash}

    async def _save(self, job: dict[str, Any]) -> None:
        await self.redis.set(JOB_KEY.format(job["job_id"]), json.dumps(job))

    async def _load(self, job_id: str) -> dict[str, Any] | None:
        raw = await self.redis.get(JOB_KEY.format(job_id))
        if raw is None:
            return None
        record: dict[str, Any] = json.loads(raw.decode() if isinstance(raw, bytes) else raw)
        return record


def summary(job: dict[str, Any], created: bool) -> dict[str, Any]:
    """The part of a job that POST /jobs returns: enough to poll it, without the receipt."""
    return {
        "job_id": job["job_id"],
        "status": job["status"],
        "task_id": job["task_id"],
        "input_hash": job["input_hash"],
        "model_id": job["model_id"],
        "deadline": job["deadline"],
        "created": created,
    }


def attestation_from_receipt(report: dict[str, Any]) -> AttestationReport:
    return AttestationReport(
        enclave_measurement=report["enclave_measurement"],
        signer_identity=report["signer_identity"],
        input_hash=report["input_hash"],
        output_hash=report["output_hash"],
        hardware_signature=base64.b64decode(report["hardware_signature"]),
        timestamp=report["timestamp"],
    )


def proof_from_receipt(zkml_proof: dict[str, Any]) -> ProofArtifact:
    return ProofArtifact(
        proof_bytes=base64.b64decode(zkml_proof["proof_bytes"]),
        public_inputs=zkml_proof["public_inputs"],
        verification_key_ref=zkml_proof["verification_key_ref"],
        backend=zkml_proof["backend"],
    )
