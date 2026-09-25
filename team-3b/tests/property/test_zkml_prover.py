import hashlib
import json

import fakeredis.aioredis as fakeredis
import pytest
from hypothesis import HealthCheck, given, settings, strategies as st

from common.exceptions import UnsupportedArchitectureError
from common.model_registry import ModelRegistry
from common.models import ExecutionReceipt
from common.constants import SUPPORTED_ARCHITECTURES
from proof_generator import ProofVerifier, ZkMLProver
from tests.property.test_model_registry_prop import MockStorageBackend


def make_receipt(model_id: str, model_cid: str, output: bytes) -> ExecutionReceipt:
    input_data = b"proof-input"
    return ExecutionReceipt(
        execution_id="execution-1",
        model_id=model_id,
        model_cid=model_cid,
        input_hash=hashlib.sha256(input_data).hexdigest(),
        output_hash=hashlib.sha256(output).hexdigest(),
        output=output,
        timestamp=1.0,
    )


@pytest.fixture
def registry_and_storage():
    redis_client = fakeredis.FakeRedis(decode_responses=False)
    storage = MockStorageBackend()
    return ModelRegistry(redis_client, storage), storage


@settings(max_examples=8, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(architecture=st.sampled_from(SUPPORTED_ARCHITECTURES), output=st.binary(min_size=1, max_size=128))
@pytest.mark.asyncio
async def test_property_6_supported_receipts_generate_and_verify(architecture, output, registry_and_storage):
    registry, storage = registry_and_storage
    model_binary = (architecture + "-private-weights").encode()
    cid = await storage.upload(model_binary)
    await registry.register("proof-model", cid, model_binary, architecture=architecture)
    prover = ZkMLProver(registry)
    verifier = ProofVerifier()

    artifact = await prover.generate_proof(make_receipt("proof-model", cid, output))

    assert verifier.verify(artifact)
    assert artifact.public_inputs["architecture"] == architecture
    assert artifact.public_inputs["model_hash"] == hashlib.sha256(model_binary).hexdigest()


@pytest.mark.asyncio
async def test_property_6_tampered_public_input_fails_independent_verifier(registry_and_storage):
    registry, storage = registry_and_storage
    model_binary = b"linear-private-weights"
    cid = await storage.upload(model_binary)
    await registry.register("tamper-model", cid, model_binary, architecture="linear")
    prover = ZkMLProver(registry)
    verifier = ProofVerifier()

    artifact = await prover.generate_proof(make_receipt("tamper-model", cid, b"output"))
    tampered_inputs = dict(artifact.public_inputs)
    tampered_inputs["output_hash"] = "0" * 64
    tampered = artifact.model_copy(update={"public_inputs": tampered_inputs})

    assert not verifier.verify(tampered)


@pytest.mark.asyncio
async def test_property_7_proof_size_is_constant_and_weights_do_not_leak(registry_and_storage):
    registry, storage = registry_and_storage
    small_weights = b"tiny-secret-linear-weights"
    large_weights = b"large-secret-mlp-weights" * 100
    small_cid = await storage.upload(small_weights)
    large_cid = await storage.upload(large_weights)
    await registry.register("small", small_cid, small_weights, architecture="linear")
    await registry.register("large", large_cid, large_weights, architecture="mlp")
    prover = ZkMLProver(registry)

    small_artifact = await prover.generate_proof(make_receipt("small", small_cid, b"small-output"))
    large_artifact = await prover.generate_proof(make_receipt("large", large_cid, b"large-output"))

    assert len(small_artifact.proof_bytes) == len(large_artifact.proof_bytes)
    small_serialized = json.dumps(
        {
            "public_inputs": small_artifact.public_inputs,
            "verification_key_ref": small_artifact.verification_key_ref,
        },
        sort_keys=True,
    ).encode() + small_artifact.proof_bytes
    large_serialized = json.dumps(
        {
            "public_inputs": large_artifact.public_inputs,
            "verification_key_ref": large_artifact.verification_key_ref,
        },
        sort_keys=True,
    ).encode() + large_artifact.proof_bytes
    assert small_weights not in small_serialized
    assert large_weights not in large_serialized


@pytest.mark.asyncio
async def test_property_8_unsupported_architecture_lists_complete_supported_set(registry_and_storage):
    registry, storage = registry_and_storage
    model_binary = b"unsupported-weights"
    cid = await storage.upload(model_binary)
    await registry.register("unsupported", cid, model_binary, architecture="rnn")
    prover = ZkMLProver(registry)

    with pytest.raises(UnsupportedArchitectureError) as error:
        await prover.generate_proof(make_receipt("unsupported", cid, b"output"))

    assert error.value.supported == SUPPORTED_ARCHITECTURES
    assert str(list(SUPPORTED_ARCHITECTURES)) in str(error.value)