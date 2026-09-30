import json, hashlib, base64, hmac
from proof_generator.verifier import ProofVerifier
from common.models import ProofArtifact

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

payload = {
    "proof_bytes": base64.urlsafe_b64encode(proof_bytes_val).decode('utf-8'),
    "public_inputs": public_inputs,
    "verification_key_ref": "data/proving_keys/transformer_small.vk"
}
proof_bytes = json.dumps(payload).encode()
artifact_payload = json.loads(proof_bytes)
artifact = ProofArtifact.model_validate(artifact_payload)

verifier = ProofVerifier()
result = verifier.verify(artifact)
print("Result:", result)
if not result:
    print("architecture check:", public_inputs.get("architecture") in ["linear", "cnn", "mlp", "transformer_small"])
    print("vk check:", artifact.verification_key_ref.endswith(f"/{public_inputs.get('architecture')}.vk"))
    expected = (
        b"DEVCOMMITMENT1"
        + hashlib.sha256(canonical_inputs).digest()
        + hashlib.sha256(f"development-commitment:circuit:transformer_small:v1".encode()).digest()
    )
    print("expected:", expected)
    print("actual  :", artifact.proof_bytes)
