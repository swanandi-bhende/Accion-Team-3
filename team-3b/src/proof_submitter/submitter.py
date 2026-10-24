"""Batched proof submission with retry, metrics, and dead-letter recovery."""

import asyncio
import hashlib
import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx

from common.constants import DEVELOPMENT_PROOF_BACKEND, PRODUCTION_PROOF_BACKENDS
from common.exceptions import ProofNotSubmittableError
from common.models import AttestationReport, ProofArtifact

logger = logging.getLogger(__name__)


@dataclass
class PendingProof:
    """A proof waiting in a batch, with the hashes it submits on chain.

    ``proof_hash`` is the SHA-256 of the zkML proof bytes and the hardware
    signature. ``result_hash`` is the proof's output hash.
    """

    task_id: str
    attestation: AttestationReport
    zkml_proof: ProofArtifact
    model_cid: str
    proof_hash: str
    result_hash: str
    submitted_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        """Return the JSON-safe summary of this proof stored in dead-letter entries."""
        return {
            "task_id": self.task_id,
            "proof_hash": self.proof_hash,
            "result_hash": self.result_hash,
            "model_cid": self.model_cid,
            "submitted_at": self.submitted_at,
        }


class ProofSubmitter:
    """Batch proofs and submit them to the proof-submitter contract.

    Proofs collected within ``batch_window`` seconds are sent together, one
    transaction per contract-sized chunk. Each chunk is retried up to ``MAX_RETRIES``
    times, skipping proofs the contract already holds. Chunks that still fail, and
    proofs the contract rejects, are written to the dead-letter queue in Redis.
    """

    BATCH_WINDOW = 10.0
    MAX_RETRIES = 3
    RETRY_INTERVAL = 10.0
    DLQ_KEY = "proof_submitter:dlq"
    ALERTS_KEY = "proof_submitter:alerts"

    def __init__(
        self,
        web3: Any,
        contract: Any,
        redis_client: Any,
        private_key: str = "",
        batch_window: float = BATCH_WINDOW,
        sleep: Callable[[float], Any] = asyncio.sleep,
        clock: Callable[[], float] = time.time,
        signer_address: str | None = None,
        allow_development_backend: bool = False,
        alert_webhook_url: str | None = None,
    ):
        self.web3 = web3
        # Where dead-letter alerts are also posted, e.g. a Slack-compatible incoming webhook.
        # The URL is never logged, because it usually carries a token.
        self.alert_webhook_url = alert_webhook_url
        self.contract = contract
        self.redis = redis_client
        self.private_key = private_key
        # On a real network the node has no unlocked accounts, so the sender must
        # be resolved from the private key before the first transaction.
        self.signer_address = signer_address
        # Off by default: development-commitment proofs are not zero-knowledge. Only a
        # test passes True, to exercise the chain path with them.
        self.allow_development_backend = allow_development_backend
        self.batch_window = batch_window
        self.sleep = sleep
        self.clock = clock
        self._batch_limit: int | None = None
        # Rejections are keyed by transaction hash so a concurrent batch can never
        # pick up another batch's rejected proofs.
        self._rejections_by_tx: dict[str, list[PendingProof]] = {}
        self._pending_proofs: list[PendingProof] = []
        self._batch_timer: asyncio.Task | None = None
        self._batch_lock = asyncio.Lock()
        self.metrics = {
            "batches_submitted": 0,
            "proofs_submitted": 0,
            "retries": 0,
            "dlq_batches": 0,
            "last_batch_size": 0,
            "rejected_by_contract": 0,
            "alerts_raised": 0,
        }

    def _may_submit(self, zkml_proof: ProofArtifact) -> bool:
        """Only zero-knowledge backends reach the chain. The development backend needs opt-in."""
        if zkml_proof.backend in PRODUCTION_PROOF_BACKENDS:
            return True
        return self.allow_development_backend and zkml_proof.backend == DEVELOPMENT_PROOF_BACKEND

    async def submit_proof(
        self,
        task_id: str,
        attestation: AttestationReport,
        zkml_proof: ProofArtifact,
        model_cid: str,
    ) -> PendingProof:
        """Queue one proof for the next batch and start the batch timer if needed.

        Returns:
            The queued ``PendingProof``. Its transaction is sent when the batch window
            ends or when ``flush`` is called.

        Raises:
            ProofNotSubmittableError: the proof's backend is not allowed on chain.
        """
        if not self._may_submit(zkml_proof):
            raise ProofNotSubmittableError(zkml_proof.backend)
        proof_hash = hashlib.sha256(zkml_proof.proof_bytes + attestation.hardware_signature).hexdigest()
        result_hash = zkml_proof.public_inputs.get("output_hash", attestation.output_hash)
        pending = PendingProof(task_id, attestation, zkml_proof, model_cid, proof_hash, result_hash, self.clock())
        async with self._batch_lock:
            self._pending_proofs.append(pending)
            if self._batch_timer is None or self._batch_timer.done():
                self._batch_timer = asyncio.create_task(self._flush_after_window())
        return pending

    async def _flush_after_window(self) -> None:
        await self.sleep(self.batch_window)
        async with self._batch_lock:
            proofs = list(self._pending_proofs)
            self._pending_proofs.clear()
            self._batch_timer = None
        if proofs:
            await self._submit_batch(proofs)

    def buffered_task_ids(self) -> list[str]:
        """Task ids still waiting for the next batch flush."""
        return [proof.task_id for proof in self._pending_proofs]

    async def flush(self) -> str | None:
        """Submit every buffered proof now instead of waiting out the batch window.

        Returns the transaction hash, or ``None`` when the batch exhausted its
        retries and was dead-lettered. Safe to call with nothing buffered.
        """
        async with self._batch_lock:
            if self._batch_timer is not None:
                self._batch_timer.cancel()
                self._batch_timer = None
            proofs = list(self._pending_proofs)
            self._pending_proofs.clear()
        if not proofs:
            return None
        return await self._submit_batch(proofs)

    async def _submit_batch(self, proofs: list[PendingProof]) -> str | None:
        """Submit ``proofs`` one contract-sized chunk at a time.

        Each chunk is retried on its own, so a chunk the contract already accepted is
        never resent because a later chunk failed. Returns the last transaction hash,
        or ``None`` when every chunk was dead-lettered.
        """
        if not proofs:
            return None
        limit = await asyncio.to_thread(self._max_batch_size) or len(proofs)
        last_tx_hash: str | None = None
        for start in range(0, len(proofs), limit):
            tx_hash = await self._submit_chunk(proofs[start:start + limit])
            if tx_hash is not None:
                last_tx_hash = tx_hash
        return last_tx_hash

    async def _submit_chunk(self, chunk: list[PendingProof]) -> str | None:
        """Send the proofs in ``chunk`` that the contract does not hold yet.

        The contract is asked before every attempt. A retry after a transaction that
        was mined but whose receipt wait failed therefore sends nothing new, and is not
        dead-lettered as a rejection of a proof that is already on chain.
        """
        last_error: Exception | None = None
        remaining = chunk
        for attempt in range(1, self.MAX_RETRIES + 1):
            remaining = await asyncio.to_thread(self._unrecorded, chunk)
            if not remaining:
                logger.info("PROOF_ALREADY_RECORDED size=%s", len(chunk))
                return None
            try:
                tx_hash = await self._send_transaction(remaining)
                rejected = self._rejections_by_tx.pop(tx_hash, [])
                await self._emit_anchoring_event(tx_hash, remaining)
                self.metrics["batches_submitted"] += 1
                self.metrics["proofs_submitted"] += len(remaining) - len(rejected)
                self.metrics["last_batch_size"] = len(remaining)
                logger.info("Proof batch submitted size=%s tx_hash=%s", len(remaining), tx_hash)
                if rejected:
                    # Team A's submitProofs returns a count and rejects individual
                    # entries with ProofRejected instead of reverting the batch.
                    logger.warning(
                        "PROOF_BATCH_PARTIAL accepted=%s rejected=%s task_ids=%s",
                        len(remaining) - len(rejected),
                        len(rejected),
                        [proof.task_id for proof in rejected],
                    )
                    self.metrics["rejected_by_contract"] += len(rejected)
                    await self._dead_letter(rejected, RuntimeError("rejected on chain via ProofRejected"))
                return tx_hash
            except Exception as error:
                last_error = error
                if attempt < self.MAX_RETRIES:
                    self.metrics["retries"] += 1
                    logger.warning(
                        "Proof batch retry attempt=%s size=%s error=%s", attempt, len(remaining), error
                    )
                    await self.sleep(self.RETRY_INTERVAL)
        await self._dead_letter(remaining, last_error)
        return None

    def _unrecorded(self, proofs: list[PendingProof]) -> list[PendingProof]:
        """The proofs the contract does not already hold with the same proof hash."""
        getter = getattr(getattr(self.contract, "functions", None), "getProof", None)
        if getter is None:
            return list(proofs)
        return [
            proof
            for proof in proofs
            if getter(self._bytes32(proof.task_id)).call()[0] != self._bytes32(proof.proof_hash)
        ]

    def _sender(self) -> str:
        if self.signer_address:
            return self.signer_address
        return str(self.web3.eth.accounts[0])

    def _max_batch_size(self) -> int | None:
        """Team A's ``MAX_PROOF_BATCH``, when this contract advertises one."""
        if self._batch_limit is None:
            factory = getattr(getattr(self.contract, "functions", None), "MAX_PROOF_BATCH", None)
            if factory is None:
                return None
            try:
                limit = int(factory().call())
            except Exception as error:  # noqa: BLE001 - older ABIs simply lack the getter
                logger.warning("Could not read MAX_PROOF_BATCH: %s", error)
                return None
            self._batch_limit = limit if limit > 0 else None
        return self._batch_limit

    async def _send_transaction(self, proofs: list[PendingProof]) -> str:
        """Send one contract-sized chunk and return its transaction hash.

        The web3 calls block, so they run in a worker thread. Contract-level
        rejections are recorded under the transaction hash, where ``_submit_chunk``
        collects them.
        """
        tx_hash, rejected = await asyncio.to_thread(self._send_chunk, proofs)
        if rejected:
            self._rejections_by_tx[tx_hash] = rejected
        return tx_hash

    def _send_chunk(self, proofs: list[PendingProof]) -> tuple[str, list[PendingProof]]:
        task_ids = [self._bytes32(proof.task_id) for proof in proofs]
        proof_hashes = [self._bytes32(proof.proof_hash) for proof in proofs]
        model_cids = [proof.model_cid for proof in proofs]
        result_hashes = [self._bytes32(proof.result_hash) for proof in proofs]
        sender = self._sender()
        transaction = self.contract.functions.submitProofs(task_ids, proof_hashes, model_cids, result_hashes).build_transaction({
            "from": sender,
            "nonce": self.web3.eth.get_transaction_count(sender),
        })
        if self.private_key:
            signed = self.web3.eth.account.sign_transaction(transaction, private_key=self.private_key)
            tx_hash = self.web3.eth.send_raw_transaction(signed.raw_transaction)
        else:
            tx_hash = self.contract.functions.submitProofs(task_ids, proof_hashes, model_cids, result_hashes).transact({
                "from": sender,
            })
        receipt = self.web3.eth.wait_for_transaction_receipt(tx_hash)
        if getattr(receipt, "status", receipt.get("status", 1) if isinstance(receipt, dict) else 1) != 1:
            raise RuntimeError("proof submission transaction reverted")
        logger.info(
            "PROOF_CONFIRMATION_LATENCY seconds=%.3f batch_size=%s",
            self.clock() - min(proof.submitted_at for proof in proofs),
            len(proofs),
        )
        normalised = str(tx_hash.hex()) if hasattr(tx_hash, "hex") else str(tx_hash)
        return normalised, self._rejected_from_receipt(receipt, proofs)

    def _rejected_from_receipt(self, receipt: Any, proofs: list[PendingProof]) -> list[PendingProof]:
        """Proofs the contract reported as rejected in this receipt.

        Empty when the ABI has no ``ProofRejected`` event (the mock) or the receipt
        cannot be decoded, in which case the batch is treated as fully accepted.
        """
        events = getattr(self.contract, "events", None)
        event_factory = getattr(events, "ProofRejected", None) if events is not None else None
        if event_factory is None:
            return []
        try:
            entries = event_factory().process_receipt(receipt)
        except Exception as error:  # noqa: BLE001 - undecodable receipt is not a rejection
            logger.debug("Could not decode ProofRejected events: %s", error)
            return []
        rejected_ids: dict[bytes, int] = {}
        for entry in entries:
            args = dict(entry["args"])
            raw_task_id = args["taskId"]
            task_id = raw_task_id.hex() if hasattr(raw_task_id, "hex") else str(raw_task_id)
            rejected_ids[self._bytes32(task_id)] = int(args["reason"])
        rejected = [proof for proof in proofs if self._bytes32(proof.task_id) in rejected_ids]
        for proof in rejected:
            logger.warning(
                "PROOF_REJECTED task_id=%s reason=%s",
                proof.task_id,
                rejected_ids[self._bytes32(proof.task_id)],
            )
        return rejected

    @staticmethod
    def _bytes32(value: str) -> bytes:
        if value.startswith("0x") and len(value) == 66:
            return bytes.fromhex(value[2:])
        if len(value) == 64:
            try:
                return bytes.fromhex(value)
            except ValueError:
                pass
        digest = hashlib.sha256(value.encode()).digest()
        return digest

    async def _emit_anchoring_event(self, tx_hash: str, proofs: list[PendingProof]) -> None:
        logger.info("PROOF_ANCHORED tx_hash=%s task_ids=%s", tx_hash, [proof.task_id for proof in proofs])

    async def _persist_to_dlq(self, proofs: list[PendingProof], error: Exception | None) -> None:
        payload = {
            "proofs": [proof.to_dict() for proof in proofs],
            "failed_at": self.clock(),
            "last_error": str(error) if error else "unknown error",
        }
        await self.redis.lpush(self.DLQ_KEY, json.dumps(payload))
        self.metrics["dlq_batches"] += 1

    async def _dead_letter(self, proofs: list[PendingProof], error: Exception | None) -> None:
        """Persist proofs to the dead-letter queue and raise the alert for them (Requirement 6.5)."""
        await self._persist_to_dlq(proofs, error)
        await self._emit_alert(proofs, error)

    async def _emit_alert(self, proofs: list[PendingProof], error: Exception | None) -> None:
        """Record the alert in Redis, log it, and post it to the webhook when one is configured.

        The Redis record is written after the post, so it carries the delivery result:
        ``not_configured``, ``delivered``, or ``failed: <exception type>``. A failed post never
        stops the dead-letter path.
        """
        task_ids = [proof.task_id for proof in proofs]
        message = str(error) if error else "unknown error"
        logger.error("PROOF_SUBMISSION_FAILED_PERMANENTLY task_ids=%s error=%s", task_ids, message)
        alert: dict[str, Any] = {
            "event": "proof_submission_failed_permanently",
            "task_ids": task_ids,
            "error": message,
            "failed_at": self.clock(),
            "text": f"Proof submission failed permanently for {len(task_ids)} task(s): {message}",
        }
        alert["delivery"] = await self._deliver_alert(alert)
        await self.redis.lpush(self.ALERTS_KEY, json.dumps(alert))
        self.metrics["alerts_raised"] += 1

    async def _deliver_alert(self, alert: dict[str, Any]) -> str:
        if not self.alert_webhook_url:
            return "not_configured"
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(self.alert_webhook_url, json=alert)
                response.raise_for_status()
        except httpx.HTTPError as error:
            # Only the exception type is reported: the exception text can include the URL.
            logger.error("ALERT_DELIVERY_FAILED error=%s", type(error).__name__)
            return f"failed: {type(error).__name__}"
        return "delivered"
