"""Batched proof submission with retry, metrics, and dead-letter recovery."""

import asyncio
import hashlib
import inspect
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from common.models import AttestationReport, ProofArtifact

logger = logging.getLogger(__name__)


@dataclass
class PendingProof:
    task_id: str
    attestation: AttestationReport
    zkml_proof: ProofArtifact
    model_cid: str
    proof_hash: str
    result_hash: str
    submitted_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "proof_hash": self.proof_hash,
            "result_hash": self.result_hash,
            "model_cid": self.model_cid,
            "submitted_at": self.submitted_at,
        }


class ProofSubmitter:
    BATCH_WINDOW = 10.0
    MAX_RETRIES = 3
    RETRY_INTERVAL = 10.0
    DLQ_KEY = "proof_submitter:dlq"

    def __init__(
        self,
        web3: Any,
        contract: Any,
        redis_client: Any,
        private_key: str = "",
        batch_window: float = BATCH_WINDOW,
        sleep: Callable[[float], Any] = asyncio.sleep,
        clock: Callable[[], float] = time.time,
    ):
        self.web3 = web3
        self.contract = contract
        self.redis = redis_client
        self.private_key = private_key
        self.batch_window = batch_window
        self.sleep = sleep
        self.clock = clock
        self._pending_proofs: list[PendingProof] = []
        self._batch_timer: asyncio.Task | None = None
        self._batch_lock = asyncio.Lock()
        self.metrics = {
            "batches_submitted": 0,
            "proofs_submitted": 0,
            "retries": 0,
            "dlq_batches": 0,
            "last_batch_size": 0,
        }

    async def submit_proof(
        self,
        task_id: str,
        attestation: AttestationReport,
        zkml_proof: ProofArtifact,
        model_cid: str,
    ) -> PendingProof:
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

    async def _submit_batch(self, proofs: list[PendingProof]) -> str | None:
        last_error: Exception | None = None
        for attempt in range(1, self.MAX_RETRIES + 1):
            try:
                tx_hash = await self._send_transaction(proofs)
                await self._emit_anchoring_event(tx_hash, proofs)
                self.metrics["batches_submitted"] += 1
                self.metrics["proofs_submitted"] += len(proofs)
                self.metrics["last_batch_size"] = len(proofs)
                logger.info("Proof batch submitted size=%s tx_hash=%s", len(proofs), tx_hash)
                return tx_hash
            except Exception as error:
                last_error = error
                if attempt < self.MAX_RETRIES:
                    self.metrics["retries"] += 1
                    logger.warning("Proof batch retry attempt=%s size=%s error=%s", attempt, len(proofs), error)
                    await self.sleep(self.RETRY_INTERVAL)
        await self._persist_to_dlq(proofs, last_error)
        await self._emit_alert(proofs, last_error)
        return None

    async def _send_transaction(self, proofs: list[PendingProof]) -> str:
        task_ids = [self._bytes32(proof.task_id) for proof in proofs]
        proof_hashes = [self._bytes32(proof.proof_hash) for proof in proofs]
        model_cids = [proof.model_cid for proof in proofs]
        result_hashes = [self._bytes32(proof.result_hash) for proof in proofs]
        transaction = self.contract.functions.submitProofs(task_ids, proof_hashes, model_cids, result_hashes).build_transaction({
            "from": self.web3.eth.accounts[0],
            "nonce": self.web3.eth.get_transaction_count(self.web3.eth.accounts[0]),
        })
        if self.private_key:
            signed = self.web3.eth.account.sign_transaction(transaction, private_key=self.private_key)
            tx_hash = self.web3.eth.send_raw_transaction(signed.raw_transaction)
        else:
            tx_hash = self.contract.functions.submitProofs(task_ids, proof_hashes, model_cids, result_hashes).transact({
                "from": self.web3.eth.accounts[0],
            })
        receipt = self.web3.eth.wait_for_transaction_receipt(tx_hash)
        if getattr(receipt, "status", receipt.get("status", 1) if isinstance(receipt, dict) else 1) != 1:
            raise RuntimeError("proof submission transaction reverted")
        logger.info(
            "PROOF_CONFIRMATION_LATENCY seconds=%.3f batch_size=%s",
            self.clock() - min(proof.submitted_at for proof in proofs),
            len(proofs),
        )
        return tx_hash.hex() if hasattr(tx_hash, "hex") else str(tx_hash)

    @staticmethod
    def _bytes32(value: str) -> bytes:
        if value.startswith("0x") and len(value) == 66:
            return bytes.fromhex(value[2:])
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

    async def _emit_alert(self, proofs: list[PendingProof], error: Exception | None) -> None:
        logger.error(
            "PROOF_SUBMISSION_FAILED_PERMANENTLY task_ids=%s error=%s",
            [proof.task_id for proof in proofs],
            error,
        )