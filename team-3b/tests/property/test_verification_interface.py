import json
import time
import base64
from typing import Dict, Any

import pytest
from hypothesis import given, strategies as st

from common.exceptions import UnsupportedFormatError
from common.models import VerificationRequest, AttestationReport
from inference.enclave_sim import EnclaveSim
from verification.interface import VerificationInterface
from verification.verifiers import ConcreteTEEVerifier, ConcreteZkMLVerifier, serialize_attestation


def _generate_valid_tee_proof_and_inputs() -> tuple[bytes, dict]:
    enclave = EnclaveSim()
    input_hash = "input_hash_1"
    output_hash = "output_hash_1"
    report = AttestationReport(
        enclave_measurement=enclave.measurement,
        signer_identity=enclave.signer_id,
        input_hash=input_hash,
        output_hash=output_hash,
        hardware_signature=b"",
        timestamp=time.time(),
    )
    signed_payload = f"{report.enclave_measurement}:{report.signer_identity}:{report.input_hash}:{report.output_hash}".encode()
    report.hardware_signature = enclave.sign(signed_payload)
    proof_bytes = serialize_attestation(report, enclave.public_key_bytes)
    return proof_bytes, {"input_hash": input_hash, "output_hash": output_hash}, enclave


class MockResolver:
    def __init__(self, data: dict):
        self.data = data
    async def resolve(self, reference: str) -> bytes:
        from verification.resolver import ProofResolutionError
        if reference in self.data:
            return self.data[reference]
        raise ProofResolutionError("Not found")


@pytest.fixture
def interface():
    enclave = EnclaveSim() # dummy enclave for keys, but tests can inject keys later
    tee_verifier = ConcreteTEEVerifier(trusted_public_keys={})
    zkml_verifier = ConcreteZkMLVerifier()
    resolver = MockResolver({})
    return VerificationInterface(tee_verifier, zkml_verifier, resolver)


@pytest.mark.asyncio
async def test_property_21_tampering(interface):
    proof_bytes, public_inputs, enclave = _generate_valid_tee_proof_and_inputs()
    interface.tee_verifier.trusted_public_keys[enclave.signer_id] = enclave.public_key_bytes

    # Valid
    req = VerificationRequest(proof_format="tee_attestation", public_inputs=public_inputs, proof_bytes=proof_bytes)
    res = await interface.verify(req)
    assert res.result is True
    assert res.public_inputs == public_inputs

    # Tamper with input_hash in public_inputs
    bad_inputs = public_inputs.copy()
    bad_inputs["input_hash"] = "bad"
    req_bad_input = VerificationRequest(proof_format="tee_attestation", public_inputs=bad_inputs, proof_bytes=proof_bytes)
    res = await interface.verify(req_bad_input)
    assert res.result is False
    assert res.reason_code == "proof_mismatch"

    # Tamper with signature
    payload = json.loads(proof_bytes)
    report_payload = payload["report"]
    report_payload["hardware_signature"] = base64.b64encode(b"bad_signature").decode()
    bad_proof_bytes = json.dumps(payload).encode()
    
    req_bad_sig = VerificationRequest(proof_format="tee_attestation", public_inputs=public_inputs, proof_bytes=bad_proof_bytes)
    res = await interface.verify(req_bad_sig)
    assert res.result is False
    assert res.reason_code == "invalid_signature"


@pytest.mark.asyncio
async def test_property_22_reference_equality(interface):
    proof_bytes, public_inputs, enclave = _generate_valid_tee_proof_and_inputs()
    interface.tee_verifier.trusted_public_keys[enclave.signer_id] = enclave.public_key_bytes

    ref = "QmTest123"
    interface.resolver.data[ref] = proof_bytes

    req_val = VerificationRequest(proof_format="tee_attestation", public_inputs=public_inputs, proof_bytes=proof_bytes)
    res_val = await interface.verify(req_val)

    req_ref = VerificationRequest(proof_format="tee_attestation", public_inputs=public_inputs, proof_reference=ref)
    res_ref = await interface.verify(req_ref)

    assert res_val.result == res_ref.result
    assert res_val.public_inputs == res_ref.public_inputs


@pytest.mark.asyncio
async def test_property_23_unsupported_formats(interface):
    with pytest.raises(UnsupportedFormatError) as exc:
        req = VerificationRequest(proof_format="invalid_format", public_inputs={}, proof_bytes=b"")
        await interface.verify(req)
    assert exc.value.supported == ["tee_attestation", "zkml_snark"]


@pytest.mark.asyncio
async def test_security_replayed_attestation(interface):
    proof_bytes, public_inputs, enclave = _generate_valid_tee_proof_and_inputs()
    interface.tee_verifier.trusted_public_keys[enclave.signer_id] = enclave.public_key_bytes

    # Attempt to verify with different task context / inputs
    different_inputs = {"input_hash": "different", "output_hash": "different"}
    req = VerificationRequest(proof_format="tee_attestation", public_inputs=different_inputs, proof_bytes=proof_bytes)
    res = await interface.verify(req)
    assert res.result is False
    assert res.reason_code == "proof_mismatch"


@pytest.mark.asyncio
async def test_security_different_enclave_keypair(interface):
    proof_bytes, public_inputs, enclave1 = _generate_valid_tee_proof_and_inputs()
    enclave2 = EnclaveSim() # second enclave

    # Server trusts enclave2, but proof was generated by enclave1
    interface.tee_verifier.trusted_public_keys[enclave2.signer_id] = enclave2.public_key_bytes
    
    # Try with proof from enclave1
    req = VerificationRequest(proof_format="tee_attestation", public_inputs=public_inputs, proof_bytes=proof_bytes)
    res = await interface.verify(req)
    assert res.result is False
    assert res.reason_code == "invalid_signature"


@given(st.binary())
def test_security_malformed_fuzzing(bad_bytes):
    # Just running a synchronous version of the check to ensure it doesn't crash but returns cleanly
    import asyncio
    interface_sync = VerificationInterface(ConcreteTEEVerifier({}), ConcreteZkMLVerifier(), MockResolver({}))
    
    async def run_check():
        req = VerificationRequest(proof_format="tee_attestation", public_inputs={}, proof_bytes=bad_bytes)
        res = await interface_sync.verify(req)
        assert res.result is False
        assert res.reason_code == "malformed_proof"
        
    asyncio.run(run_check())
