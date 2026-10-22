"""Composed execution pipeline: task event -> inference -> proof -> settlement.

Coupling choice (Step 16.2, argued in full in ``docs/ARCHITECTURE.md``):

* **Between the Event Listener and the task executor** the handoff is a bounded
  ``asyncio.Queue``. The listener's chain-poll loop must never be blocked by a
  slow TEE inference, and a burst of funded tasks should buffer instead of
  stampeding the TEE. This queue is the seam Step 17 replaces with Redis
  Streams, so the in-process wiring and the multi-container deployment share the
  same shape.
* **Between the stages of one task** the handoff is a direct awaited call. Each
  stage consumes the previous stage's artifact, so an intermediate queue would
  add latency and a mid-flight checkpointing burden while decoupling nothing.

Concurrency is capped twice on purpose: ``max_in_flight`` worker tasks bound
end-to-end concurrency, and an ``asyncio.Semaphore`` around the inference stage
bounds TEE concurrency even for callers that bypass the queue (tests, a future
Redis Streams consumer).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import signal
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import numpy as np
import redis.asyncio as redis
from web3 import Web3

from common.config import Settings, get_settings
from common.exceptions import (
    IntegrityCheckFailedError,
    ModelExecutionError,
    ModelNotFoundError,
    ProofGenerationError,
    ProofInconsistentError,
    ProofNotSubmittableError,
    ProvingKeysUnavailableError,
    StorageUnavailableError,
    TEEUnavailableError,
    TimeoutExceededError,
    UnsupportedArchitectureError,
)
from common.model_registry import ModelRegistry
from common.models import ExecutionReceipt, InferenceRequest, VerificationRequest
from common.storage.ipfs_backend import IPFSBackend
from common.tensors import encode_tensor
from event_listener.config import load_contract_abi
from event_listener.listener import EventListener
from inference.engine import InferenceEngine
from inference.tee_runtime import TEERuntime
from proof_generator.zkml_prover import ZkMLProver
from proof_submitter.submitter import ProofSubmitter
from verification.app import build_verification_interface
from verification.verifiers import serialize_proof_artifact

logger = logging.getLogger(__name__)

OUTCOME_SUBMITTED = "submitted"
OUTCOME_DROPPED_MODEL_NOT_FOUND = "dropped_model_not_found"
OUTCOME_DROPPED_INFERENCE_TIMEOUT = "dropped_inference_timeout"
OUTCOME_DROPPED_INTEGRITY_FAILURE = "dropped_integrity_failure"
OUTCOME_DROPPED_TEE_UNAVAILABLE = "dropped_tee_unavailable"
OUTCOME_DROPPED_INFERENCE_ERROR = "dropped_inference_error"
OUTCOME_DROPPED_PROOF_TIMEOUT = "dropped_proof_timeout"
OUTCOME_DROPPED_UNSUPPORTED_ARCHITECTURE = "dropped_unsupported_architecture"
OUTCOME_DROPPED_SUBMISSION_FAILED = "dropped_submission_failed"
OUTCOME_DROPPED_PROOF_NOT_SUBMITTABLE = "dropped_proof_not_submittable"
OUTCOME_DROPPED_PROOF_KEYS_MISSING = "dropped_proof_keys_missing"
OUTCOME_DROPPED_PROOF_INCONSISTENT = "dropped_proof_inconsistent"
OUTCOME_DROPPED_PROOF_FAILED = "dropped_proof_failed"

# Placeholder input until tasks carry their own inputs (phase 2, step 5). A 1x4
# float32 zero tensor matches the toy models the seed service registers.
PLACEHOLDER_INPUT = encode_tensor(np.zeros((1, 4), dtype=np.float32))


class TaskDropped(Exception):
    """Internal control flow: a stage decided this task cannot proceed.

    Every stage boundary converts its own typed failures into this one signal, so
    ``run_task`` has a single, explicit place to decide a task's fate instead of
    letting an unexpected exception tear down the event loop.
    """

    def __init__(self, outcome: str, detail: str = ""):
        self.outcome = outcome
        self.detail = detail
        super().__init__(f"{outcome}: {detail}" if detail else outcome)


@dataclass
class TaskRecord:
    """One task's journey through the pipeline, for correlation and latency."""

    task_id: str
    outcome: str
    started_at: float
    total_s: float = 0.0
    model_id: str = ""
    execution_id: str | None = None
    proof_hash: str | None = None
    tx_hash: str | None = None
    inference_s: float | None = None
    proof_s: float | None = None
    submit_s: float | None = None
    confirmation_s: float | None = None
    verified: bool | None = None
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        """Return the correlation, outcome and timing fields as a plain dict."""
        return {
            "task_id": self.task_id,
            "execution_id": self.execution_id,
            "model_id": self.model_id,
            "outcome": self.outcome,
            "proof_hash": self.proof_hash,
            "tx_hash": self.tx_hash,
            "verified": self.verified,
            "inference_s": self.inference_s,
            "proof_s": self.proof_s,
            "submit_s": self.submit_s,
            "confirmation_s": self.confirmation_s,
            "total_s": self.total_s,
            "detail": self.detail,
        }


class Pipeline:
    """Own and drive the five stages in one process."""

    MAX_INFERENCE_ATTEMPTS = 3
    RETRY_BACKOFF_BASE_S = 0.5

    def __init__(
        self,
        listener: Any,
        engine: Any,
        tee: Any,
        prover: Any,
        submitter: Any,
        verification: Any = None,
        max_in_flight: int = 4,
        input_provider: Callable[[dict[str, Any]], bytes] | None = None,
        flush_batches: bool = False,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], Any] = asyncio.sleep,
        queue_maxsize: int | None = None,
    ):
        if max_in_flight < 1:
            raise ValueError("max_in_flight must be >= 1")
        self.listener = listener
        self.engine = engine
        self.tee = tee
        self.prover = prover
        self.submitter = submitter
        self.verification = verification
        self.max_in_flight = max_in_flight
        self.input_provider = input_provider or self._default_input
        # Settlement normally waits out the submitter's batch window. Tests that
        # assert a confirmed transaction turn this on to force a flush per task.
        self.flush_batches = flush_batches
        self.clock = clock
        self.sleep = sleep

        self.records: list[TaskRecord] = []
        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(
            maxsize=queue_maxsize or max_in_flight * 2
        )
        self._inference_slots = asyncio.Semaphore(max_in_flight)
        self._flush_lock = asyncio.Lock()
        self._workers: list[asyncio.Task] = []
        self._in_flight = 0
        self._inference_in_flight = 0
        self._peak_inference_in_flight = 0
        self._throttled_events = 0
        self._outcome_counts: dict[str, int] = {}

        # Fan the listener out through the queue instead of into a stage directly.
        self.listener.dispatch = self.enqueue

    # ------------------------------------------------------------------ #
    # Admission and worker pool
    # ------------------------------------------------------------------ #

    async def enqueue(self, task: dict[str, Any]) -> None:
        """Admission point used as the Event Listener's dispatch target."""
        if self._queue.full():
            self._throttled_events += 1
            logger.warning(
                "PIPELINE_BACKPRESSURE task_id=%s queue_depth=%s queue_maxsize=%s in_flight=%s action=throttle",
                self._task_id(task),
                self._queue.qsize(),
                self._queue.maxsize,
                self._in_flight,
            )
        await self._queue.put(task)
        logger.info(
            "PIPELINE_ENQUEUED task_id=%s queue_depth=%s", self._task_id(task), self._queue.qsize()
        )

    async def start(self, poll_interval: float = 1.0) -> None:
        """Start the worker pool and the listener's poll loop (blocks forever)."""
        self._spawn_workers()
        logger.info(
            "PIPELINE_STARTED max_in_flight=%s queue_maxsize=%s",
            self.max_in_flight,
            self._queue.maxsize,
        )
        await self.listener.start(poll_interval)

    def _spawn_workers(self) -> None:
        if self._workers:
            return
        self._workers = [
            asyncio.create_task(self._worker(index), name=f"pipeline-worker-{index}")
            for index in range(self.max_in_flight)
        ]

    async def _worker(self, index: int) -> None:
        while True:
            task = await self._queue.get()
            try:
                await self.run_task(task)
            except asyncio.CancelledError:
                raise
            except Exception:
                # run_task absorbs every per-stage failure; anything reaching here
                # is a wiring bug and must not silently kill the worker.
                logger.exception("PIPELINE_WORKER_ERROR worker=%s", index)
            finally:
                self._queue.task_done()

    async def stop(self) -> None:
        """Cancel the worker tasks and wait for them to exit."""
        for worker in self._workers:
            worker.cancel()
        await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers = []

    async def drain(self) -> list[TaskRecord]:
        """Process everything already queued. Bounded only by the inference cap.

        Used by integration tests and by a graceful shutdown, where the listener
        is not running but buffered work must still be settled.
        """
        pending = []
        while not self._queue.empty():
            pending.append(asyncio.create_task(self._drain_one(self._queue.get_nowait())))
        if not pending:
            return []
        return list(await asyncio.gather(*pending))

    async def _drain_one(self, task: dict[str, Any]) -> TaskRecord:
        try:
            return await self.run_task(task)
        finally:
            self._queue.task_done()

    async def flush_submission(self) -> str | None:
        """Force out every proof buffered by the submitter. Returns the tx hash."""
        tx_hash: str | None = await self.submitter.flush()
        return tx_hash

    # ------------------------------------------------------------------ #
    # The per-task stage chain
    # ------------------------------------------------------------------ #

    def _default_input(self, task: dict[str, Any]) -> bytes:
        return PLACEHOLDER_INPUT

    @staticmethod
    def _task_id(task: dict[str, Any]) -> str:
        """Read the id from the listener's payload, which nests it under ``created``."""
        return str(task["created"]["task_id"])

    def _build_request(self, task: dict[str, Any]) -> InferenceRequest:
        created = task["created"]
        return InferenceRequest(
            model_id=created["description_hash"].hex(),
            input_data=self.input_provider(task),
            task_id=self._task_id(task),
        )

    async def run_task(self, task: dict[str, Any]) -> TaskRecord:
        """Walk one task through every stage, recording its outcome either way."""
        task_id = self._task_id(task)
        record = TaskRecord(task_id=task_id, outcome=OUTCOME_SUBMITTED, started_at=self.clock())
        execution_id: str | None = None
        self._in_flight += 1
        try:
            request = self._build_request(task)
            record.model_id = request.model_id
            receipt, inference_s = await self._run_inference(request, task_id)
            record.inference_s = inference_s
            execution_id = receipt.execution_id
            record.execution_id = execution_id
            attestation, proof, proof_s = await self._run_proof(
                request, receipt, task_id, execution_id
            )
            record.proof_s = proof_s
            record.proof_hash = hashlib.sha256(proof.proof_bytes).hexdigest()
            record.submit_s, record.confirmation_s, record.tx_hash = await self._run_submission(
                task_id, execution_id, attestation, proof, receipt.model_cid
            )
            record.verified = await self._run_verification(proof, task_id, execution_id)
        except TaskDropped as drop:
            record.outcome = drop.outcome
            record.detail = drop.detail
        except asyncio.CancelledError:
            raise
        except Exception as error:  # last-resort guard: never kill the poll loop
            record.outcome = OUTCOME_DROPPED_INFERENCE_ERROR
            record.detail = f"{type(error).__name__}: {error}"
            logger.exception(
                "PIPELINE_UNEXPECTED_ERROR task_id=%s execution_id=%s", task_id, execution_id
            )
        finally:
            self._in_flight -= 1
            record.total_s = self.clock() - record.started_at
        self._finalize(record)
        return record

    def _finalize(self, record: TaskRecord) -> None:
        self.records.append(record)
        self._outcome_counts[record.outcome] = self._outcome_counts.get(record.outcome, 0) + 1
        logger.info(
            "PIPELINE_TASK_COMPLETE task_id=%s execution_id=%s outcome=%s inference_s=%s proof_s=%s total_s=%.3f",
            record.task_id,
            record.execution_id,
            record.outcome,
            _format(record.inference_s),
            _format(record.proof_s),
            record.total_s,
        )

    async def _run_inference(
        self, request: InferenceRequest, task_id: str
    ) -> tuple[ExecutionReceipt, float]:
        """Stage 1/2: TEE-backed inference, bounded and retried on infrastructure faults."""
        attempt = 0
        while True:
            attempt += 1
            started = self.clock()
            try:
                async with self._inference_slots:
                    self._inference_in_flight += 1
                    self._peak_inference_in_flight = max(
                        self._peak_inference_in_flight, self._inference_in_flight
                    )
                    try:
                        receipt = await self.engine.execute(request)
                    finally:
                        self._inference_in_flight -= 1
            except (StorageUnavailableError, TEEUnavailableError, OSError) as error:
                if attempt >= self.MAX_INFERENCE_ATTEMPTS:
                    outcome = (
                        OUTCOME_DROPPED_TEE_UNAVAILABLE
                        if isinstance(error, TEEUnavailableError)
                        else OUTCOME_DROPPED_INFERENCE_ERROR
                    )
                    raise TaskDropped(
                        outcome, f"{type(error).__name__}: {error} (after {attempt} attempts)"
                    ) from error
                delay = self.RETRY_BACKOFF_BASE_S * (2 ** (attempt - 1))
                logger.warning(
                    "PIPELINE_INFERENCE_RETRY task_id=%s attempt=%s/%s delay_s=%s error=%s",
                    task_id,
                    attempt,
                    self.MAX_INFERENCE_ATTEMPTS,
                    delay,
                    error,
                )
                await self.sleep(delay)
            except ModelExecutionError as error:
                raise TaskDropped(OUTCOME_DROPPED_INFERENCE_ERROR, str(error)) from error
            except IntegrityCheckFailedError as error:
                raise TaskDropped(OUTCOME_DROPPED_INTEGRITY_FAILURE, str(error)) from error
            except ModelNotFoundError as error:
                raise TaskDropped(OUTCOME_DROPPED_MODEL_NOT_FOUND, str(error)) from error
            except TimeoutExceededError as error:
                # Retrying would only blow the task deadline again; drop it and
                # keep the queue moving for every other task.
                raise TaskDropped(OUTCOME_DROPPED_INFERENCE_TIMEOUT, str(error)) from error
            except ValueError as error:
                raise TaskDropped(OUTCOME_DROPPED_INFERENCE_ERROR, str(error)) from error
            else:
                return receipt, self.clock() - started

    async def _run_proof(
        self, request: InferenceRequest, receipt: Any, task_id: str, execution_id: str
    ) -> tuple[Any, Any, float]:
        """Stage 3: TEE attestation, then the zkML proof over the receipt."""
        started = self.clock()
        try:
            attestation = self.tee.generate_attestation(request.input_data, receipt.output)
        except TEEUnavailableError as error:
            raise TaskDropped(OUTCOME_DROPPED_TEE_UNAVAILABLE, str(error)) from error
        try:
            proof = await self.prover.generate_proof(receipt, request.input_data)
        except TimeoutError as error:
            raise TaskDropped(
                OUTCOME_DROPPED_PROOF_TIMEOUT, f"zkML proof exceeded its timeout: {error}"
            ) from error
        except UnsupportedArchitectureError as error:
            raise TaskDropped(OUTCOME_DROPPED_UNSUPPORTED_ARCHITECTURE, str(error)) from error
        except ProvingKeysUnavailableError as error:
            raise TaskDropped(OUTCOME_DROPPED_PROOF_KEYS_MISSING, str(error)) from error
        except ProofInconsistentError as error:
            raise TaskDropped(OUTCOME_DROPPED_PROOF_INCONSISTENT, str(error)) from error
        except ProofGenerationError as error:
            raise TaskDropped(OUTCOME_DROPPED_PROOF_FAILED, str(error)) from error
        logger.info(
            "PIPELINE_PROOF_READY task_id=%s execution_id=%s architecture=%s public_inputs=%s",
            task_id,
            execution_id,
            proof.public_inputs.get("architecture"),
            json.dumps(proof.public_inputs, sort_keys=True),
        )
        return attestation, proof, self.clock() - started

    async def _run_submission(
        self,
        task_id: str,
        execution_id: str,
        attestation: Any,
        proof: Any,
        model_cid: str,
    ) -> tuple[float, float | None, str | None]:
        """Stage 4: hand the proof to the batched on-chain submitter."""
        started = self.clock()
        try:
            pending = await self.submitter.submit_proof(task_id, attestation, proof, model_cid)
        except ProofNotSubmittableError as error:
            raise TaskDropped(OUTCOME_DROPPED_PROOF_NOT_SUBMITTABLE, str(error)) from error
        except Exception as error:
            raise TaskDropped(
                OUTCOME_DROPPED_SUBMISSION_FAILED, f"{type(error).__name__}: {error}"
            ) from error
        buffered_s = self.clock() - started
        logger.info(
            "PIPELINE_PROOF_BUFFERED task_id=%s execution_id=%s proof_hash=%s buffered_s=%.3f",
            task_id,
            execution_id,
            pending.proof_hash,
            buffered_s,
        )
        if not self.flush_batches:
            return buffered_s, None, None
        return (buffered_s, *await self._settle(pending, task_id, execution_id))

    async def _settle(
        self, pending: Any, task_id: str, execution_id: str
    ) -> tuple[float | None, str | None]:
        """Flush this task's batch and report whether it reached the chain."""
        async with self._flush_lock:
            if task_id not in self.submitter.buffered_task_ids():
                # An earlier flush already took this proof as part of its batch.
                return None, None
            dlq_before = self.submitter.metrics["dlq_batches"]
            started = self.clock()
            tx_hash = await self.submitter.flush()
            confirmation_s = self.clock() - started
            if self.submitter.metrics["dlq_batches"] > dlq_before:
                raise TaskDropped(
                    OUTCOME_DROPPED_SUBMISSION_FAILED,
                    f"proof for task_id={task_id} exhausted retries and was dead-lettered",
                )
            logger.info(
                "PIPELINE_SETTLED task_id=%s execution_id=%s tx_hash=%s confirmation_s=%.3f",
                task_id,
                execution_id,
                tx_hash,
                confirmation_s,
            )
            return confirmation_s, tx_hash

    async def _run_verification(self, proof: Any, task_id: str, execution_id: str) -> bool | None:
        """Stage 5: re-verify the settled proof through the public interface.

        A mismatch is logged loudly but does not un-submit a settled task; it is
        evidence to investigate, not a reason to drop a task that is already on
        chain.
        """
        if self.verification is None:
            return None
        request = VerificationRequest(
            proof_format="zkml_snark",
            public_inputs=proof.public_inputs,
            proof_bytes=serialize_proof_artifact(proof),
        )
        response = await self.verification.verify(request)
        logger.info(
            "PIPELINE_VERIFICATION task_id=%s execution_id=%s result=%s reason_code=%s",
            task_id,
            execution_id,
            response.result,
            response.reason_code,
        )
        if not response.result:
            logger.warning(
                "PIPELINE_VERIFICATION_MISMATCH task_id=%s execution_id=%s reason_code=%s",
                task_id,
                execution_id,
                response.reason_code,
            )
        return bool(response.result)

    # ------------------------------------------------------------------ #
    # Observability
    # ------------------------------------------------------------------ #

    def stats(self) -> dict[str, Any]:
        """Queue depth, concurrency and outcome counters for tests and ops."""
        return {
            "queued": self._queue.qsize(),
            "in_flight": self._in_flight,
            "inference_in_flight": self._inference_in_flight,
            "peak_inference_in_flight": self._peak_inference_in_flight,
            "max_in_flight": self.max_in_flight,
            "workers": len(self._workers),
            "throttled_events": self._throttled_events,
            "outcome_counts": dict(self._outcome_counts),
            "submitter_metrics": dict(getattr(self.submitter, "metrics", {})),
        }

    def latency_report(self) -> dict[str, Any]:
        """Dispatch-to-settlement latency percentiles over every recorded task."""
        if not self.records:
            return {"tasks": 0}
        return {
            "tasks": len(self.records),
            "outcomes": dict(self._outcome_counts),
            "total_s": _summarize([record.total_s for record in self.records]),
            "inference_s": _summarize(
                [record.inference_s for record in self.records if record.inference_s is not None]
            ),
            "proof_s": _summarize(
                [record.proof_s for record in self.records if record.proof_s is not None]
            ),
            "confirmation_s": _summarize(
                [record.confirmation_s for record in self.records if record.confirmation_s is not None]
            ),
        }


def _format(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.3f}"


def _summarize(values: list[float]) -> dict[str, float] | None:
    if not values:
        return None
    ordered = sorted(values)

    def percentile(fraction: float) -> float:
        """Return the sorted value at the given fraction, without interpolation."""
        return ordered[min(len(ordered) - 1, int(fraction * len(ordered)))]

    return {
        "min": ordered[0],
        "p50": percentile(0.5),
        "p95": percentile(0.95),
        "max": ordered[-1],
        "mean": sum(ordered) / len(ordered),
    }


def build_pipeline(settings: Settings | None = None, **overrides: Any) -> Pipeline:
    """Wire every stage from ``common/config.py`` — the one env source of truth.

    ``python -m inference.pipeline`` runs exactly this wiring, so the composed
    service and the integration tests cannot drift apart.

    Contract ABIs are resolved from ``TASK_MANAGER_ARTIFACT`` and ``ESCROW_ARTIFACT``
    in ``CONTRACT_ARTIFACTS_DIR``, the same inputs the event listener uses.
    """
    settings = settings or get_settings().require(
        "redis_url",
        "web3_rpc_url",
        "task_manager_address",
        "escrow_address",
        "tee_mode",
    )
    redis_client = redis.from_url(settings.redis_url, decode_responses=False)
    storage = IPFSBackend(node_url=settings.model_storage_url or "http://localhost:5001")
    registry = ModelRegistry(redis_client, storage)

    tee = TEERuntime(settings.tee_mode or "")
    tee.initialize()
    engine = InferenceEngine(
        registry,
        tee,
        redis_client,
        timeout_s=settings.tee_inference_timeout_s,
        sealed_key=settings.sealed_key_bytes(),
    )
    prover = ZkMLProver(
        registry,
        proving_key_store=settings.zkml_proving_key_store,
        backend=settings.zkml_backend,
    )

    web3 = Web3(Web3.HTTPProvider(settings.web3_rpc_url))
    task_manager = web3.eth.contract(
        address=web3.to_checksum_address(settings.task_manager_address or ""),
        abi=load_contract_abi(settings.task_manager_artifact),
    )
    escrow = web3.eth.contract(
        address=web3.to_checksum_address(settings.escrow_address or ""),
        abi=load_contract_abi(settings.escrow_artifact),
    )
    submitter = ProofSubmitter(
        web3,
        task_manager,
        redis_client,
        private_key=settings.proof_submitter_private_key,
        batch_window=settings.proof_batch_window_s,
        allow_development_backend=settings.proof_allow_development_backend,
    )
    listener = EventListener(web3, task_manager, escrow, redis_client)

    return Pipeline(
        listener,
        engine,
        tee,
        prover,
        submitter,
        verification=build_verification_interface(settings, storage),
        max_in_flight=settings.pipeline_max_in_flight,
        **overrides,
    )


async def _main() -> None:
    pipeline = build_pipeline()
    logger.info("PIPELINE_CONFIG max_in_flight=%s", pipeline.max_in_flight)
    # As PID 1 in a container the process ignores SIGTERM unless it installs a handler, so
    # `docker stop` would wait out its grace period and kill it. Stopping the listener ends
    # the poll loop, and the finally block cancels the workers (and their proving children).
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, pipeline.listener.stop)
    try:
        await pipeline.start()
    finally:
        await pipeline.stop()
    logger.info("PIPELINE_STOPPED")


if __name__ == "__main__":
    logging.basicConfig(level=get_settings().log_level.upper())
    try:
        asyncio.run(_main())
    except KeyboardInterrupt:
        pass
