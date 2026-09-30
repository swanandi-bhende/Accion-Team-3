import json
import time
import base64
import hashlib
from typing import Dict, Any

import pytest
import httpx
from hypothesis import given, strategies as st, settings, HealthCheck

from common.exceptions import UnsupportedFormatError
from common.models import VerificationRequest, AttestationReport, ProofArtifact
from common.constants import SUPPORTED_VERIFICATION_FORMATS, TEE_ATTESTATION_EXPIRY_S
from inference.enclave_sim import EnclaveSim
from verification.interface import VerificationInterface
from verification.verifiers import ConcreteTEEVerifier, ConcreteZkMLVerifier, serialize_attestation
from verification.resolver import StorageProofResolver, ProofResolutionError
from verification.app import create_app


# Helper to generate TEE Proof
def _generate_valid_tee_proof_and_inputs(expired: bool = False) -> tuple[bytes, dict, EnclaveSim]:
    enclave = EnclaveSim()
    input_hash = "input_hash_1"
    output_hash = "output_hash_1"
    
    timestamp = time.time()
    if expired:
        timestamp -= (TEE_ATTESTATION_EXPIRY_S + 10)
        
    report = AttestationReport(
        enclave_measurement=enclave.measurement,
        signer_identity=enclave.signer_id,
        input_hash=input_hash,
        output_hash=output_hash,
        hardware_signature=b"",
        timestamp=timestamp,
    )
    signed_payload = f"{report.enclave_measurement}:{report.signer_identity}:{report.input_hash}:{report.output_hash}".encode()
    report.hardware_signature = enclave.sign(signed_payload)
    proof_bytes = serialize_attestation(report, enclave.public_key_bytes)
    return proof_bytes, {"input_hash": input_hash, "output_hash": output_hash}, enclave


# Helper to generate ZKML Proof
def _generate_valid_zkml_proof_and_inputs() -> tuple[bytes, dict]:
    public_inputs = {
        "input_hash": "input_hash_1",
        "output_hash": "output_hash_1",
        "model_hash": "dummy_model_hash",
        "architecture": "transformer_small"
    }
    canonical_inputs = json.dumps(public_inputs, sort_keys=True, separators=(",", ":")).encode()
    commitment = hashlib.sha256(canonical_inputs).digest()
    circuit_digest = hashlib.sha256(b"development-commitment:circuit:transformer_small:v1").digest()
    proof_bytes_val = b"DEVCOMMITMENT1" + commitment + circuit_digest
    
    import base64
    payload = {
        "proof_bytes": base64.b64encode(proof_bytes_val).decode('utf-8'),
        "public_inputs": public_inputs,
        "verification_key_ref": "data/proving_keys/transformer_small.vk"
    }
    proof_bytes = json.dumps(payload).encode()
    return proof_bytes, public_inputs


class MockStorage:
    def __init__(self):
        self.data = {}
    async def store(self, data: bytes) -> str:
        cid = f"Qm{hash(data)}"
        self.data[cid] = data
        return cid
    async def retrieve(self, cid: str) -> bytes:
        if cid in self.data:
            return self.data[cid]
        raise Exception("Not found")


@pytest.fixture
def test_setup():
    enclave = EnclaveSim() 
    tee_verifier = ConcreteTEEVerifier(trusted_public_keys={})
    zkml_verifier = ConcreteZkMLVerifier()
    
    storage = MockStorage()
    resolver = StorageProofResolver(storage=storage)
    interface = VerificationInterface(tee_verifier, zkml_verifier, resolver)
    app = create_app(interface)
    
    return interface, app, tee_verifier, storage


# 13.2 & 13.3 & 13.6
@pytest.mark.asyncio
@pytest.mark.parametrize("proof_format", ["tee_attestation", "zkml_snark"])
async def test_property_21_reason_codes(test_setup, proof_format):
    interface, _, tee_verifier, _ = test_setup
    
    # 1. Valid case
    start_time = time.time()
    if proof_format == "tee_attestation":
        proof_bytes, public_inputs, enclave = _generate_valid_tee_proof_and_inputs()
        tee_verifier.trusted_public_keys[enclave.signer_id] = enclave.public_key_bytes
    else:
        proof_bytes, public_inputs = _generate_valid_zkml_proof_and_inputs()
    
    req = VerificationRequest(proof_format=proof_format, public_inputs=public_inputs, proof_bytes=proof_bytes)
    res = await interface.verify(req)
    assert res.result is True
    assert time.time() - start_time < 5.0
    
    # 2. proof_mismatch
    start_time = time.time()
    bad_inputs = public_inputs.copy()
    bad_inputs["input_hash"] = "bad"
    req_bad_input = VerificationRequest(proof_format=proof_format, public_inputs=bad_inputs, proof_bytes=proof_bytes)
    res = await interface.verify(req_bad_input)
    assert res.result is False
    assert res.reason_code == "proof_mismatch"
    assert time.time() - start_time < 5.0

    # 3. malformed_proof
    start_time = time.time()
    req_malformed = VerificationRequest(proof_format=proof_format, public_inputs=public_inputs, proof_bytes=b"garbage")
    res = await interface.verify(req_malformed)
    assert res.result is False
    assert res.reason_code == "malformed_proof"
    assert time.time() - start_time < 5.0

    # 4. format-specific failures
    start_time = time.time()
    if proof_format == "tee_attestation":
        # invalid_signature
        payload = json.loads(proof_bytes)
        payload["report"]["hardware_signature"] = base64.b64encode(b"bad_signature").decode()
        bad_sig_bytes = json.dumps(payload).encode()
        req_bad_sig = VerificationRequest(proof_format=proof_format, public_inputs=public_inputs, proof_bytes=bad_sig_bytes)
        res = await interface.verify(req_bad_sig)
        assert res.result is False
        assert res.reason_code == "invalid_signature"
        assert time.time() - start_time < 5.0

        # expired_attestation
        exp_proof_bytes, exp_public_inputs, exp_enclave = _generate_valid_tee_proof_and_inputs(expired=True)
        tee_verifier.trusted_public_keys[exp_enclave.signer_id] = exp_enclave.public_key_bytes
        req_exp = VerificationRequest(proof_format=proof_format, public_inputs=exp_public_inputs, proof_bytes=exp_proof_bytes)
        res = await interface.verify(req_exp)
        assert res.result is False
        assert res.reason_code == "expired_attestation"
        assert time.time() - start_time < 5.0
        
        # untrusted key
        start_time = time.time()
        untrusted_proof_bytes, untrusted_public_inputs, untrusted_enclave = _generate_valid_tee_proof_and_inputs()
        # Do not add untrusted_enclave to trusted_public_keys
        req_untrusted = VerificationRequest(proof_format=proof_format, public_inputs=untrusted_public_inputs, proof_bytes=untrusted_proof_bytes)
        res = await interface.verify(req_untrusted)
        assert res.result is False
        assert res.reason_code == "invalid_signature"
        assert time.time() - start_time < 5.0


# 13.4
@pytest.mark.asyncio
async def test_property_22_reference_equality_genuine_path(test_setup):
    interface, _, tee_verifier, storage = test_setup
    
    proof_bytes, public_inputs, enclave = _generate_valid_tee_proof_and_inputs()
    tee_verifier.trusted_public_keys[enclave.signer_id] = enclave.public_key_bytes

    # Actually store via storage
    cid = await storage.store(proof_bytes)

    req_val = VerificationRequest(proof_format="tee_attestation", public_inputs=public_inputs, proof_bytes=proof_bytes)
    res_val = await interface.verify(req_val)

    req_ref = VerificationRequest(proof_format="tee_attestation", public_inputs=public_inputs, proof_reference=cid)
    res_ref = await interface.verify(req_ref)

    assert res_val.result == res_ref.result
    assert res_val.public_inputs == res_ref.public_inputs
    assert res_val.reason_code == res_ref.reason_code


# 13.5
@settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(st.text())
def test_property_23_unsupported_formats_generated(test_setup_sync, fmt):
    interface = test_setup_sync
    import asyncio
    
    if fmt in SUPPORTED_VERIFICATION_FORMATS:
        return
        
    async def run_check():
        start_time = time.time()
        with pytest.raises(UnsupportedFormatError) as exc:
            req = VerificationRequest(proof_format=fmt, public_inputs={}, proof_bytes=b"")
            await interface.verify(req)
        assert exc.value.supported == SUPPORTED_VERIFICATION_FORMATS
        assert time.time() - start_time < 5.0
        
    asyncio.run(run_check())


@pytest.fixture
def test_setup_sync():
    enclave = EnclaveSim() 
    tee_verifier = ConcreteTEEVerifier(trusted_public_keys={})
    zkml_verifier = ConcreteZkMLVerifier()
    resolver = StorageProofResolver(storage=MockStorage())
    return VerificationInterface(tee_verifier, zkml_verifier, resolver)


# 13.7
@pytest.mark.asyncio
async def test_property_suite_via_fastapi(test_setup):
    interface, app, tee_verifier, storage = test_setup
    proof_bytes, public_inputs, enclave = _generate_valid_tee_proof_and_inputs()
    tee_verifier.trusted_public_keys[enclave.signer_id] = enclave.public_key_bytes

    # Encode bytes to string for JSON transmission
    proof_str = proof_bytes.decode('utf-8')

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # Valid test
        req_data = {
            "proof_format": "tee_attestation",
            "public_inputs": public_inputs,
            "proof_bytes": proof_str
        }
        res = await client.post("/verify", json=req_data)
        assert res.status_code == 200
        data = res.json()
        assert data["result"] is True, f"Failed: {data}"
        assert data["public_inputs"] == public_inputs

        # Malformed format (Property 23 equivalent via API)
        req_data_fmt = {
            "proof_format": "invalid_fmt",
            "public_inputs": public_inputs,
            "proof_bytes": proof_str
        }
        res = await client.post("/verify", json=req_data_fmt)
        assert res.status_code == 400
        data = res.json()
        assert data["supported"] == SUPPORTED_VERIFICATION_FORMATS

        # Proof mismatch (Property 21 equivalent via API)
        bad_inputs = public_inputs.copy()
        bad_inputs["input_hash"] = "bad"
        req_data_mismatch = {
            "proof_format": "tee_attestation",
            "public_inputs": bad_inputs,
            "proof_bytes": proof_str
        }
        res = await client.post("/verify", json=req_data_mismatch)
        assert res.status_code == 200
        data = res.json()
        assert data["result"] is False
        assert data["reason_code"] == "proof_mismatch"
