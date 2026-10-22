"""Property tests for the Verification Interface (Properties 21-24).

Property 21: every outcome is correct and carries the exact ``reason_code``
    for its specific failure mode (one dedicated case per reason code).
Property 22: verifying a proof by value and by reference is equivalent,
    resolved through a real storage/transaction reference path.
Property 23: unsupported proof formats are rejected with the supported list.
Property 24: every verification path completes within the 5-second bound
    (Requirement 9.1), including malformed-input failures.

The full reason-code matrix runs against both ``tee_attestation`` and
``zkml_snark``; the TEE verifier owns the signature- and expiry-based reasons
while the zkML verifier owns the proof-parsing-based reasons.
"""

import asyncio
import base64
import hashlib
import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, NoReturn, cast

import httpx
import pytest
from fastapi import FastAPI
from hypothesis import example, given, settings
from hypothesis import strategies as st

from common.constants import SUPPORTED_VERIFICATION_FORMATS, TEE_ATTESTATION_EXPIRY_S
from common.exceptions import UnsupportedFormatError
from common.interfaces import StorageBackend
from common.models import AttestationReport, VerificationRequest, VerificationResponse
from inference.enclave_sim import EnclaveSim
from verification.app import create_app, create_default_app
from verification.interface import VerificationInterface
from verification.resolver import ProofResolutionError, StorageProofResolver
from verification.verifiers import (
    ConcreteTEEVerifier,
    ConcreteZkMLVerifier,
    serialize_attestation,
)

TEE_FORMAT = "tee_attestation"
ZKML_FORMAT = "zkml_snark"
ZKML_ARCHITECTURE = "transformer_small"
LATENCY_BUDGET_S = 5.0
TEE_ONLY_REASON_CODES = {"invalid_signature", "expired_attestation"}

CaseBuilder = Callable[[], tuple[VerificationInterface, dict[str, str], bytes]]


# --------------------------------------------------------------------------- #
# Reference backend, interface and proof fixtures
# --------------------------------------------------------------------------- #

class InMemoryStorage(StorageBackend):
    """A real ``StorageBackend`` so reference resolution is genuinely exercised."""

    def __init__(self) -> None:
        self._objects: dict[str, bytes] = {}
        self.retrieve_calls = 0

    async def upload(self, data: bytes) -> str:
        cid = "Qm" + hashlib.sha256(data).hexdigest()
        self._objects[cid] = data
        return cid

    async def retrieve(self, cid: str) -> bytes:
        self.retrieve_calls += 1
        if cid not in self._objects:
            raise ProofResolutionError(f"unknown CID: {cid}")
        return self._objects[cid]


def _build_interface(
    storage: StorageBackend | None = None,
) -> tuple[VerificationInterface, ConcreteTEEVerifier, StorageBackend]:
    storage = storage if storage is not None else InMemoryStorage()
    tee_verifier = ConcreteTEEVerifier(trusted_public_keys={})
    resolver = StorageProofResolver(storage=storage)
    interface = VerificationInterface(tee_verifier, ConcreteZkMLVerifier(), resolver)
    return interface, tee_verifier, storage


def _tee_proof(
    expired: bool = False, corrupt_signature: bool = False
) -> tuple[bytes, dict[str, str], EnclaveSim]:
    """Mint a fresh simulated attestation plus the data needed to verify it."""
    enclave = EnclaveSim()
    input_hash, output_hash = "input_hash_1", "output_hash_1"
    timestamp = time.time() - (TEE_ATTESTATION_EXPIRY_S + 10 if expired else 0)
    signed_payload = f"{enclave.measurement}:{enclave.signer_id}:{input_hash}:{output_hash}".encode()
    signature = enclave.sign(signed_payload)
    if corrupt_signature:
        signature = bytes(byte ^ 0xFF for byte in signature)
    report = AttestationReport(
        enclave_measurement=enclave.measurement,
        signer_identity=enclave.signer_id,
        input_hash=input_hash,
        output_hash=output_hash,
        hardware_signature=signature,
        timestamp=timestamp,
    )
    proof_bytes = serialize_attestation(report, enclave.public_key_bytes)
    return proof_bytes, {"input_hash": input_hash, "output_hash": output_hash}, enclave


def _zkml_proof() -> tuple[bytes, dict[str, str]]:
    public_inputs = {
        "input_hash": "input_hash_1",
        "output_hash": "output_hash_1",
        "model_hash": "dummy_model_hash",
        "architecture": ZKML_ARCHITECTURE,
    }
    canonical = json.dumps(public_inputs, sort_keys=True, separators=(",", ":")).encode()
    proof_value = (
        b"DEVCOMMITMENT1"
        + hashlib.sha256(canonical).digest()
        + hashlib.sha256(f"development-commitment:circuit:{ZKML_ARCHITECTURE}:v1".encode()).digest()
    )
    payload = {
        "proof_bytes": base64.b64encode(proof_value).decode(),
        "public_inputs": public_inputs,
        "verification_key_ref": f"data/proving_keys/{ZKML_ARCHITECTURE}.vk",
    }
    return json.dumps(payload).encode(), public_inputs


def _trust(tee_verifier: ConcreteTEEVerifier, enclave: EnclaveSim) -> None:
    tee_verifier.trusted_public_keys[enclave.signer_id] = enclave.public_key_bytes


async def _verify(
    interface: VerificationInterface,
    proof_format: str,
    public_inputs: dict[str, str],
    *,
    proof_bytes: bytes | None = None,
    proof_reference: str | None = None,
) -> tuple[VerificationResponse, float]:
    request = VerificationRequest(
        proof_format=proof_format,
        public_inputs=public_inputs,
        proof_bytes=proof_bytes,
        proof_reference=proof_reference,
    )
    started = time.perf_counter()
    response = await interface.verify(request)
    return response, time.perf_counter() - started


def _assert_equivalent(left: VerificationResponse, right: VerificationResponse) -> None:
    """Property 22 assertion: the two modes agree field-for-field."""
    assert left.result == right.result
    assert left.public_inputs == right.public_inputs
    assert left.reason_code == right.reason_code


# --------------------------------------------------------------------------- #
# Property 21 - one dedicated case per reason code (13.2), both formats (13.3)
# --------------------------------------------------------------------------- #

def _case_tee_wrong_signing_key() -> tuple[VerificationInterface, dict[str, str], bytes]:
    proof_bytes, public_inputs, enclave = _tee_proof()
    interface, tee_verifier, _ = _build_interface()
    tee_verifier.trusted_public_keys[enclave.signer_id] = EnclaveSim().public_key_bytes
    return interface, public_inputs, proof_bytes


def _case_tee_corrupt_signature() -> tuple[VerificationInterface, dict[str, str], bytes]:
    proof_bytes, public_inputs, enclave = _tee_proof(corrupt_signature=True)
    interface, tee_verifier, _ = _build_interface()
    _trust(tee_verifier, enclave)
    return interface, public_inputs, proof_bytes


def _case_tee_expired() -> tuple[VerificationInterface, dict[str, str], bytes]:
    proof_bytes, public_inputs, enclave = _tee_proof(expired=True)
    interface, tee_verifier, _ = _build_interface()
    _trust(tee_verifier, enclave)
    return interface, public_inputs, proof_bytes


def _case_tee_mismatch() -> tuple[VerificationInterface, dict[str, str], bytes]:
    proof_bytes, public_inputs, enclave = _tee_proof()
    interface, tee_verifier, _ = _build_interface()
    _trust(tee_verifier, enclave)
    return interface, {**public_inputs, "output_hash": "tampered_output"}, proof_bytes


def _case_tee_malformed() -> tuple[VerificationInterface, dict[str, str], bytes]:
    interface, _, _ = _build_interface()
    return interface, {"input_hash": "x", "output_hash": "y"}, b"not-a-json-attestation"


def _case_zkml_mismatch() -> tuple[VerificationInterface, dict[str, str], bytes]:
    proof_bytes, public_inputs = _zkml_proof()
    interface, _, _ = _build_interface()
    return interface, {**public_inputs, "output_hash": "tampered_output"}, proof_bytes


def _case_zkml_malformed() -> tuple[VerificationInterface, dict[str, str], bytes]:
    interface, _, _ = _build_interface()
    return interface, {"input_hash": "x", "output_hash": "y"}, b"{not valid json"


REASON_CODE_CASES = [
    pytest.param(TEE_FORMAT, "invalid_signature", _case_tee_wrong_signing_key, id="tee-invalid_signature-wrong-key"),
    pytest.param(TEE_FORMAT, "invalid_signature", _case_tee_corrupt_signature, id="tee-invalid_signature-corrupt-bytes"),
    pytest.param(TEE_FORMAT, "expired_attestation", _case_tee_expired, id="tee-expired_attestation"),
    pytest.param(TEE_FORMAT, "proof_mismatch", _case_tee_mismatch, id="tee-proof_mismatch"),
    pytest.param(TEE_FORMAT, "malformed_proof", _case_tee_malformed, id="tee-malformed_proof"),
    pytest.param(ZKML_FORMAT, "proof_mismatch", _case_zkml_mismatch, id="zkml-proof_mismatch"),
    pytest.param(ZKML_FORMAT, "malformed_proof", _case_zkml_malformed, id="zkml-malformed_proof"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("proof_format", [TEE_FORMAT, ZKML_FORMAT])
async def test_property_21_valid_proof_verifies(proof_format: str) -> None:
    interface, tee_verifier, _ = _build_interface()
    if proof_format == TEE_FORMAT:
        proof_bytes, public_inputs, enclave = _tee_proof()
        _trust(tee_verifier, enclave)
    else:
        proof_bytes, public_inputs = _zkml_proof()

    response, elapsed = await _verify(interface, proof_format, public_inputs, proof_bytes=proof_bytes)

    assert response.result is True
    assert response.reason_code is None
    assert response.public_inputs == public_inputs
    assert elapsed < LATENCY_BUDGET_S


@pytest.mark.asyncio
@pytest.mark.parametrize("proof_format, expected_reason, case_builder", REASON_CODE_CASES)
async def test_property_21_exact_reason_code_per_failure(
    proof_format: str, expected_reason: str, case_builder: CaseBuilder
) -> None:
    interface, public_inputs, proof_bytes = case_builder()

    response, elapsed = await _verify(interface, proof_format, public_inputs, proof_bytes=proof_bytes)

    assert response.result is False
    assert response.reason_code == expected_reason
    assert response.public_inputs is None
    assert elapsed < LATENCY_BUDGET_S


@pytest.mark.asyncio
async def test_property_21_zkml_never_reuses_tee_reason_codes() -> None:
    proof_bytes, public_inputs = _zkml_proof()
    interface, _, _ = _build_interface()

    response, _ = await _verify(
        interface, ZKML_FORMAT, {**public_inputs, "input_hash": "tampered"}, proof_bytes=proof_bytes
    )

    assert response.reason_code == "proof_mismatch"
    assert response.reason_code not in TEE_ONLY_REASON_CODES


@pytest.mark.asyncio
@pytest.mark.parametrize("proof_format", [TEE_FORMAT, ZKML_FORMAT])
async def test_property_21_missing_proof_is_malformed(proof_format: str) -> None:
    interface, _, _ = _build_interface()

    response, elapsed = await _verify(interface, proof_format, {"input_hash": "x"})

    assert response.result is False
    assert response.reason_code == "malformed_proof"
    assert elapsed < LATENCY_BUDGET_S


@pytest.mark.asyncio
async def test_property_21_reference_without_resolver_is_malformed() -> None:
    interface = VerificationInterface(
        ConcreteTEEVerifier(trusted_public_keys={}), ConcreteZkMLVerifier(), resolver=None
    )

    response, _ = await _verify(interface, TEE_FORMAT, {"input_hash": "x"}, proof_reference="QmAnything")

    assert response.result is False
    assert response.reason_code == "malformed_proof"


# --------------------------------------------------------------------------- #
# Property 22 - by-reference vs by-value through a genuine resolution path (13.4)
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
@pytest.mark.parametrize("proof_format", [TEE_FORMAT, ZKML_FORMAT])
async def test_property_22_by_reference_matches_by_value(proof_format: str) -> None:
    storage = InMemoryStorage()
    interface, tee_verifier, _ = _build_interface(storage)
    if proof_format == TEE_FORMAT:
        proof_bytes, public_inputs, enclave = _tee_proof()
        _trust(tee_verifier, enclave)
    else:
        proof_bytes, public_inputs = _zkml_proof()
    reference = await storage.upload(proof_bytes)

    by_value, value_elapsed = await _verify(interface, proof_format, public_inputs, proof_bytes=proof_bytes)
    by_reference, reference_elapsed = await _verify(
        interface, proof_format, public_inputs, proof_reference=reference
    )

    assert by_value.result is True
    assert by_reference.result is True
    _assert_equivalent(by_value, by_reference)
    assert by_reference.public_inputs == public_inputs
    assert storage.retrieve_calls == 1
    assert value_elapsed < LATENCY_BUDGET_S
    assert reference_elapsed < LATENCY_BUDGET_S


@pytest.mark.asyncio
@pytest.mark.parametrize("proof_format", [TEE_FORMAT, ZKML_FORMAT])
async def test_property_22_tampered_inputs_fail_identically(proof_format: str) -> None:
    storage = InMemoryStorage()
    interface, tee_verifier, _ = _build_interface(storage)
    if proof_format == TEE_FORMAT:
        proof_bytes, public_inputs, enclave = _tee_proof()
        _trust(tee_verifier, enclave)
    else:
        proof_bytes, public_inputs = _zkml_proof()
    reference = await storage.upload(proof_bytes)
    tampered_inputs = {**public_inputs, "output_hash": "tampered_output"}

    by_value, _ = await _verify(interface, proof_format, tampered_inputs, proof_bytes=proof_bytes)
    by_reference, _ = await _verify(interface, proof_format, tampered_inputs, proof_reference=reference)

    assert by_value.result is False
    assert by_reference.result is False
    _assert_equivalent(by_value, by_reference)
    assert by_reference.reason_code == "proof_mismatch"


@pytest.mark.asyncio
async def test_property_22_unresolved_reference_is_malformed() -> None:
    interface, _, _ = _build_interface(InMemoryStorage())

    response, elapsed = await _verify(interface, TEE_FORMAT, {"input_hash": "x"}, proof_reference="QmMissingCid")

    assert response.result is False
    assert response.reason_code == "malformed_proof"
    assert elapsed < LATENCY_BUDGET_S


class _FakeFunction:
    fn_name = "submitProofs"


class _FakeContract:
    def __init__(self, arguments: dict[str, list[bytes]]) -> None:
        self._arguments = arguments

    def decode_function_input(self, _data: bytes) -> tuple[_FakeFunction, dict[str, list[bytes]]]:
        return _FakeFunction(), self._arguments


class _FakeEth:
    def __init__(self, transaction: dict[str, bytes]) -> None:
        self._transaction = transaction

    def get_transaction_receipt(self, _reference: str) -> dict[str, int]:
        return {"status": 1}

    def get_transaction(self, _reference: str) -> dict[str, bytes]:
        return self._transaction


class _FakeWeb3:
    def __init__(self, transaction: dict[str, bytes]) -> None:
        self.eth = _FakeEth(transaction)


@pytest.mark.asyncio
async def test_property_22_transaction_reference_matches_resolved_value() -> None:
    resolver = StorageProofResolver(
        storage=InMemoryStorage(),
        web3=_FakeWeb3({"input": b"\x00"}),
        contract=_FakeContract({"proofHashes": [b"\x01\x02"]}),
    )
    reference = "0x" + "ab" * 32
    resolved = await resolver.resolve(reference)

    interface = VerificationInterface(
        ConcreteTEEVerifier(trusted_public_keys={}), ConcreteZkMLVerifier(), resolver
    )
    public_inputs = {"input_hash": "x", "output_hash": "y"}

    by_reference, _ = await _verify(interface, TEE_FORMAT, public_inputs, proof_reference=reference)
    by_value, _ = await _verify(interface, TEE_FORMAT, public_inputs, proof_bytes=resolved)

    _assert_equivalent(by_reference, by_value)


@pytest.mark.asyncio
async def test_property_22_unsupported_reference_is_rejected() -> None:
    resolver = StorageProofResolver(storage=InMemoryStorage())

    with pytest.raises(ProofResolutionError):
        await resolver.resolve("not-a-cid-or-transaction")


@pytest.mark.asyncio
async def test_property_22_unknown_transaction_is_rejected() -> None:
    resolver = StorageProofResolver(
        storage=InMemoryStorage(),
        web3=_FakeWeb3({"input": b"\x00"}),
        contract=_FakeContract({"proofHashes": []}),
    )
    resolver.web3.eth.get_transaction_receipt = lambda _reference: None

    with pytest.raises(ProofResolutionError):
        await resolver.resolve("0x" + "cd" * 32)


@pytest.mark.asyncio
async def test_property_22_transaction_without_decoder_is_rejected() -> None:
    resolver = StorageProofResolver(
        storage=InMemoryStorage(), web3=_FakeWeb3({"input": b"\x00"}), contract=None
    )

    with pytest.raises(ProofResolutionError):
        await resolver.resolve("0x" + "cd" * 32)


@pytest.mark.asyncio
async def test_property_22_non_submitproofs_transaction_is_rejected() -> None:
    class _WrongFunction:
        fn_name = "somethingElse"

    class _WrongContract:
        def decode_function_input(self, _data: bytes) -> tuple[_WrongFunction, dict[str, list[bytes]]]:
            return _WrongFunction(), {}

    resolver = StorageProofResolver(
        storage=InMemoryStorage(), web3=_FakeWeb3({"input": b"\x00"}), contract=_WrongContract()
    )

    with pytest.raises(ProofResolutionError):
        await resolver.resolve("0x" + "cd" * 32)


@pytest.mark.asyncio
async def test_property_22_undecodable_transaction_is_rejected() -> None:
    class _BrokenContract:
        def decode_function_input(self, _data: bytes) -> NoReturn:
            raise ValueError("bad payload")

    resolver = StorageProofResolver(
        storage=InMemoryStorage(), web3=_FakeWeb3({"input": b"\x00"}), contract=_BrokenContract()
    )

    with pytest.raises(ProofResolutionError):
        await resolver.resolve("0x" + "cd" * 32)


@pytest.mark.asyncio
async def test_property_22_transaction_lookup_failure_is_rejected() -> None:
    resolver = StorageProofResolver(
        storage=InMemoryStorage(),
        web3=_FakeWeb3({"input": b"\x00"}),
        contract=_FakeContract({"proofHashes": []}),
    )
    resolver.web3.eth.get_transaction = lambda _reference: 1 / 0

    with pytest.raises(ProofResolutionError):
        await resolver.resolve("0x" + "cd" * 32)


# --------------------------------------------------------------------------- #
# Property 23 - live, generated set of bad format strings (13.5)
# --------------------------------------------------------------------------- #

INVALID_FORMATS = [
    "",
    " ",
    "TEE_Attestation",
    "TEE_ATTESTATION",
    "tee_attestations",
    " tee_attestation",
    "tee_attestation ",
    "tee_attestation\n",
    "zkml-snark",
    "snark",
    "tee",
    "not_a_format",
]


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_format", INVALID_FORMATS)
async def test_property_23_rejects_known_bad_formats(bad_format: str) -> None:
    interface, _, _ = _build_interface()
    started = time.perf_counter()

    with pytest.raises(UnsupportedFormatError) as excinfo:
        await _verify(interface, bad_format, {"input_hash": "x"}, proof_bytes=b"ignored")

    assert excinfo.value.proof_format == bad_format
    assert excinfo.value.supported == SUPPORTED_VERIFICATION_FORMATS
    assert time.perf_counter() - started < LATENCY_BUDGET_S


@settings(max_examples=100, deadline=None)
@given(bad_format=st.text().filter(lambda value: value not in SUPPORTED_VERIFICATION_FORMATS))
@example("TEE_Attestation")
@example("")
def test_property_23_rejects_generated_formats(bad_format: str) -> None:
    interface, _, _ = _build_interface()

    async def run() -> None:
        with pytest.raises(UnsupportedFormatError) as excinfo:
            await _verify(interface, bad_format, {"input_hash": "x"}, proof_bytes=b"ignored")
        assert excinfo.value.supported == SUPPORTED_VERIFICATION_FORMATS

    asyncio.run(run())


# --------------------------------------------------------------------------- #
# Property 24 - latency budget across every path, including failures (13.6)
# --------------------------------------------------------------------------- #

@settings(max_examples=50, deadline=None)
@given(payload=st.binary(max_size=256))
def test_property_24_malformed_payloads_fail_fast(payload: bytes) -> None:
    interface, _, _ = _build_interface()

    async def run() -> None:
        response, elapsed = await _verify(interface, TEE_FORMAT, {"input_hash": "x"}, proof_bytes=payload)
        assert response.result is False
        assert elapsed < LATENCY_BUDGET_S

    asyncio.run(run())


# --------------------------------------------------------------------------- #
# Property 21-24 over the real FastAPI surface over HTTP (13.7)
# --------------------------------------------------------------------------- #

async def _post(client: httpx.AsyncClient, **payload: Any) -> httpx.Response:
    return await client.post("/verify", json=payload)


@pytest.mark.asyncio
@pytest.mark.parametrize("proof_format", [TEE_FORMAT, ZKML_FORMAT])
async def test_property_21_and_24_over_http(proof_format: str) -> None:
    interface, tee_verifier, _ = _build_interface()
    if proof_format == TEE_FORMAT:
        proof_bytes, public_inputs, enclave = _tee_proof()
        _trust(tee_verifier, enclave)
    else:
        proof_bytes, public_inputs = _zkml_proof()
    transport = httpx.ASGITransport(app=create_app(interface))

    async with httpx.AsyncClient(transport=transport, base_url="http://verify") as client:
        started = time.perf_counter()
        valid = await _post(
            client,
            proof_format=proof_format,
            public_inputs=public_inputs,
            proof_bytes=proof_bytes.decode(),
        )
        assert time.perf_counter() - started < LATENCY_BUDGET_S
        assert valid.status_code == 200
        assert valid.json()["result"] is True
        assert valid.json()["public_inputs"] == public_inputs

        mismatch = await _post(
            client,
            proof_format=proof_format,
            public_inputs={**public_inputs, "output_hash": "tampered_output"},
            proof_bytes=proof_bytes.decode(),
        )
        assert mismatch.status_code == 200
        assert mismatch.json()["result"] is False
        assert mismatch.json()["reason_code"] == "proof_mismatch"

        malformed = await _post(
            client, proof_format=proof_format, public_inputs=public_inputs, proof_bytes="not-a-real-proof"
        )
        assert malformed.status_code == 200
        assert malformed.json()["reason_code"] == "malformed_proof"


@pytest.mark.asyncio
async def test_property_22_over_http_by_reference_matches_by_value() -> None:
    storage = InMemoryStorage()
    interface, tee_verifier, _ = _build_interface(storage)
    proof_bytes, public_inputs, enclave = _tee_proof()
    _trust(tee_verifier, enclave)
    reference = await storage.upload(proof_bytes)
    transport = httpx.ASGITransport(app=create_app(interface))

    async with httpx.AsyncClient(transport=transport, base_url="http://verify") as client:
        by_value = (
            await _post(
                client,
                proof_format=TEE_FORMAT,
                public_inputs=public_inputs,
                proof_bytes=proof_bytes.decode(),
            )
        ).json()
        by_reference = (
            await _post(
                client,
                proof_format=TEE_FORMAT,
                public_inputs=public_inputs,
                proof_reference=reference,
            )
        ).json()

    assert by_value["result"] is True
    assert by_reference["result"] is True
    assert by_value["public_inputs"] == by_reference["public_inputs"] == public_inputs
    assert by_value.get("reason_code") == by_reference.get("reason_code")


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_format", ["TEE_Attestation", "tee_attestations", ""])
async def test_property_23_over_http_returns_400_with_supported_list(bad_format: str) -> None:
    interface, _, _ = _build_interface()
    transport = httpx.ASGITransport(app=create_app(interface))

    async with httpx.AsyncClient(transport=transport, base_url="http://verify") as client:
        response = await _post(client, proof_format=bad_format, public_inputs={}, proof_bytes="")

    assert response.status_code == 400
    assert response.json()["supported"] == SUPPORTED_VERIFICATION_FORMATS


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_format", [None, 123, ["tee_attestation"], {"format": "tee_attestation"}])
async def test_property_23_over_http_rejects_non_string_formats(bad_format: object) -> None:
    interface, _, _ = _build_interface()
    transport = httpx.ASGITransport(app=create_app(interface))

    async with httpx.AsyncClient(transport=transport, base_url="http://verify") as client:
        response = await _post(client, proof_format=bad_format, public_inputs={}, proof_bytes="")

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_default_app_entrypoint_serves_verification() -> None:
    """The documented ``uvicorn ...:create_default_app`` entrypoint must serve."""
    transport = httpx.ASGITransport(app=create_default_app())

    async with httpx.AsyncClient(transport=transport, base_url="http://verify") as client:
        health = await client.get("/health")
        assert health.status_code == 200
        assert health.json() == {"status": "ok"}

        response = await _post(client, proof_format="TEE_Attestation", public_inputs={}, proof_bytes="")

    assert response.status_code == 400
    assert response.json()["supported"] == SUPPORTED_VERIFICATION_FORMATS


@pytest.mark.asyncio
async def test_default_app_honors_tee_trusted_keys_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``TEE_TRUSTED_KEYS_PATH`` is a real trust anchor, not dead config.

    The same attestation must be rejected as ``invalid_signature`` when no key
    file is configured and accepted once the signer's key is trusted, so a
    misconfigured path cannot silently degrade into "reject everything" or, far
    worse, "trust everything".
    """
    monkeypatch.delenv("TEE_TRUSTED_KEYS_PATH", raising=False)
    proof_bytes, public_inputs, enclave = _tee_proof()
    payload = {
        "proof_format": TEE_FORMAT,
        "public_inputs": public_inputs,
        "proof_bytes": proof_bytes.decode(),
    }

    async def verify_with(app: FastAPI) -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://verify") as client:
            return await client.post("/verify", json=payload)

    untrusted = await verify_with(create_default_app())
    assert untrusted.status_code == 200
    assert untrusted.json()["result"] is False
    assert untrusted.json()["reason_code"] == "invalid_signature"

    keys_path = tmp_path / "trusted_keys.json"
    keys_path.write_text(
        json.dumps({enclave.signer_id: enclave.public_key_bytes.hex()}), encoding="utf-8"
    )
    monkeypatch.setenv("TEE_TRUSTED_KEYS_PATH", str(keys_path))

    # ``get_settings`` is lru_cached inside ``create_default_app``, so after
    # ``monkeypatch.setenv`` we must clear the cache so the factory re-reads the env.
    import common.config
    common.config.get_settings.cache_clear()

    trusted = await verify_with(create_default_app())
    assert trusted.status_code == 200
    assert trusted.json()["result"] is True
    assert trusted.json()["public_inputs"] == public_inputs


@pytest.mark.asyncio
async def test_verification_http_health_endpoint() -> None:
    interface, _, _ = _build_interface()
    transport = httpx.ASGITransport(app=create_app(interface))

    async with httpx.AsyncClient(transport=transport, base_url="http://verify") as client:
        response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


class _SlowInterface:
    def __init__(self, delay: float) -> None:
        self.delay = delay

    async def verify(self, _request: VerificationRequest) -> VerificationResponse:
        await asyncio.sleep(self.delay)
        raise AssertionError("verify should have been cut off by the API timeout")


@pytest.mark.asyncio
async def test_verification_http_times_out_gracefully() -> None:
    slow = cast(VerificationInterface, _SlowInterface(delay=0.5))
    transport = httpx.ASGITransport(app=create_app(slow, timeout_s=0.01))

    async with httpx.AsyncClient(transport=transport, base_url="http://verify") as client:
        response = await _post(client, proof_format=TEE_FORMAT, public_inputs={}, proof_bytes="")

    assert response.status_code == 408
    assert response.json()["reason_code"] == "verification_timeout"
