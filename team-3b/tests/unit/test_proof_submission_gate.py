"""Only zero-knowledge proofs reach the chain by default.

Development-commitment proofs are not zero-knowledge, so the submitter refuses them unless
a test opts in explicitly. An artifact that does not name its backend is refused too.
"""

import asyncio
from types import SimpleNamespace

import fakeredis.aioredis as fakeredis
import pytest

import proof_submitter.submitter as submitter_module
from common import constants
from common.constants import DEVELOPMENT_PROOF_BACKEND
from common.exceptions import ProofNotSubmittableError
from common.models import AttestationReport, ProofArtifact
from inference.pipeline import (
    OUTCOME_DROPPED_PROOF_NOT_SUBMITTABLE,
    Pipeline,
    TaskDropped,
)
from proof_submitter import ProofSubmitter


def attestation() -> AttestationReport:
    return AttestationReport(
        enclave_measurement="measurement",
        signer_identity="signer",
        input_hash="input",
        output_hash="output",
        hardware_signature=b"signature",
        timestamp=1.0,
    )


def proof(backend: str) -> ProofArtifact:
    return ProofArtifact(
        proof_bytes=b"proof",
        public_inputs={"output_hash": "output"},
        verification_key_ref="vk",
        backend=backend,
    )


async def never_wakes(delay: float) -> None:
    await asyncio.Event().wait()


def submitter(allow_development_backend: bool = False) -> ProofSubmitter:
    return ProofSubmitter(
        web3=SimpleNamespace(),
        contract=SimpleNamespace(),
        redis_client=fakeredis.FakeRedis(decode_responses=False),
        sleep=never_wakes,
        allow_development_backend=allow_development_backend,
    )


@pytest.mark.asyncio
async def test_development_proof_is_refused_by_default() -> None:
    instance = submitter()

    with pytest.raises(ProofNotSubmittableError) as refused:
        await instance.submit_proof("task", attestation(), proof(DEVELOPMENT_PROOF_BACKEND), "QmModel")

    assert refused.value.backend == DEVELOPMENT_PROOF_BACKEND
    assert instance._pending_proofs == []
    assert instance._batch_timer is None


@pytest.mark.asyncio
async def test_proof_without_a_backend_is_refused_even_with_the_development_opt_in() -> None:
    instance = submitter(allow_development_backend=True)

    with pytest.raises(ProofNotSubmittableError):
        await instance.submit_proof("task", attestation(), proof("unknown"), "QmModel")

    assert instance._pending_proofs == []


@pytest.mark.asyncio
async def test_development_proof_is_queued_only_with_the_explicit_opt_in() -> None:
    instance = submitter(allow_development_backend=True)

    try:
        pending = await instance.submit_proof("task", attestation(), proof(DEVELOPMENT_PROOF_BACKEND), "QmModel")
    finally:
        instance._batch_timer.cancel()

    assert pending.task_id == "task"
    assert len(instance._pending_proofs) == 1


@pytest.mark.asyncio
async def test_production_backend_is_accepted_without_any_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(submitter_module, "PRODUCTION_PROOF_BACKENDS", frozenset({"ezkl"}))
    instance = submitter()

    try:
        await instance.submit_proof("task", attestation(), proof("ezkl"), "QmModel")
    finally:
        instance._batch_timer.cancel()

    assert len(instance._pending_proofs) == 1


def test_only_the_ezkl_backend_is_a_production_backend() -> None:
    assert constants.PRODUCTION_PROOF_BACKENDS == frozenset({constants.EZKL_PROOF_BACKEND})
    assert constants.DEVELOPMENT_PROOF_BACKEND not in constants.PRODUCTION_PROOF_BACKENDS


@pytest.mark.asyncio
async def test_pipeline_records_a_refused_proof_as_its_own_outcome() -> None:
    pipeline = Pipeline(SimpleNamespace(), None, None, None, submitter())

    with pytest.raises(TaskDropped) as dropped:
        await pipeline._run_submission("task", "execution", attestation(), proof(DEVELOPMENT_PROOF_BACKEND), "QmModel")

    assert dropped.value.outcome == OUTCOME_DROPPED_PROOF_NOT_SUBMITTABLE
