import hashlib
from unittest.mock import patch

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from common.exceptions import IntegrityCheckFailedError
from inference.tee_runtime import TEERuntime


@pytest.fixture
def runtime() -> TEERuntime:
    rt = TEERuntime("sgx")
    rt.initialize()
    return rt

@settings(max_examples=20)
@given(model_binary=st.binary(min_size=16, max_size=1000), input_data=st.binary(min_size=1, max_size=100))
@pytest.mark.asyncio
async def test_property_4_integrity_check_and_erase(model_binary: bytes, input_data: bytes) -> None:
    """Property 4: Mismatching hash raises IntegrityCheckFailedError and calls secure_erase."""
    rt = TEERuntime("sgx")
    rt.initialize()
    assert rt.enclave is not None

    # We must encrypt the model_binary first so decrypt doesn't fail on GCM tag
    # For property test of hash failure, we encrypt a DIFFERENT binary, so decryption succeeds
    # but the hash of the decrypted data doesn't match `model_hash`.
    sealed_key = b"0123456789abcdef0123456789abcdef"
    import os

    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    aesgcm = AESGCM(sealed_key)
    nonce = os.urandom(12)

    # Decrypted binary will just be model_binary
    encrypted_data = nonce + aesgcm.encrypt(nonce, model_binary, None)

    wrong_hash = hashlib.sha256(b"wrong").hexdigest()

    with patch.object(rt.enclave, 'secure_erase', wraps=rt.enclave.secure_erase) as mock_erase:
        with pytest.raises(IntegrityCheckFailedError):
            await rt.execute_inference(encrypted_data, wrong_hash, input_data, sealed_key)

        # Verify secure_erase was actually called
        mock_erase.assert_called_once_with(model_binary)

@settings(max_examples=20)
@given(input_data=st.binary(min_size=1, max_size=100), output_data=st.binary(min_size=1, max_size=100))
def test_property_5_attestation_fields(input_data: bytes, output_data: bytes) -> None:
    """Property 5: AttestationReport has correct fields and hashes."""
    rt = TEERuntime("sgx")
    rt.initialize()
    assert rt.enclave is not None

    report = rt.generate_attestation(input_data, output_data)

    expected_input_hash = hashlib.sha256(input_data).hexdigest()
    expected_output_hash = hashlib.sha256(output_data).hexdigest()

    assert report.input_hash == expected_input_hash
    assert report.output_hash == expected_output_hash
    assert report.enclave_measurement == rt.enclave.measurement
    assert report.signer_identity == rt.enclave.signer_id
    assert len(report.hardware_signature) > 0
    assert report.timestamp > 0
