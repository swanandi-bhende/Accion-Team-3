"""Architecture-aware proof generation boundary.

Two backends sit behind the same API:

- ``ezkl`` is the production backend. It proves the model's circuit with EZKL, and
  the proof is zero-knowledge. It needs a proving bundle for the model (see
  ``src/proof_generator/ezkl_backend.py``) and the ``ezkl`` package.
- ``development`` is an explicitly non-production commitment. It keeps the API and
  privacy boundary testable without the toolchain. It is not zero-knowledge, and it is
  never submitted on chain (see ``proof_submitter``).
"""

import asyncio
import hashlib
import hmac
from dataclasses import dataclass
from pathlib import Path

from common.constants import (
    DEVELOPMENT_PROOF_BACKEND,
    EZKL_PROOF_BACKEND,
    SUPPORTED_ARCHITECTURES,
)
from common.exceptions import UnsupportedArchitectureError
from common.model_registry import ModelRegistry
from common.models import ExecutionReceipt, ProofArtifact
from common.tensors import decode_tensor
from proof_generator import ezkl_backend
from proof_generator.verifier import commitment_proof

# The configuration names (ZKML_BACKEND) and the proof backend each one produces.
BACKEND_PROOF_NAMES = {"ezkl": EZKL_PROOF_BACKEND, "development": DEVELOPMENT_PROOF_BACKEND}


@dataclass(frozen=True)
class CompiledCircuit:
    """Circuit record for one architecture and the verification key path it points to.

    With the development backend this is only a record: no circuit is compiled.
    """

    architecture: str
    verification_key_ref: str


class ZkMLProver:
    """Generate architecture-specific proof artifacts behind a stable API."""

    SUPPORTED_ARCHITECTURES = tuple(SUPPORTED_ARCHITECTURES)
    PROOF_TIMEOUT_S = 120.0

    def __init__(
        self,
        registry: ModelRegistry,
        proving_key_store: str = "data/proving_keys",
        timeout_s: float = PROOF_TIMEOUT_S,
        backend: str = "development",
    ):
        if backend not in BACKEND_PROOF_NAMES:
            raise ValueError(f"unknown zkML backend {backend!r}; expected one of {sorted(BACKEND_PROOF_NAMES)}")
        if backend == "ezkl":
            ezkl_backend.load_ezkl()  # fail at construction, not on the first task
        self.registry = registry
        self.proving_key_store = Path(proving_key_store)
        self.timeout_s = timeout_s
        # The proof backend name this prover produces, which is what each artifact records.
        self.backend = BACKEND_PROOF_NAMES[backend]
        self._circuit_cache: dict[str, CompiledCircuit] = {}
        self._circuit_locks = {architecture: asyncio.Lock() for architecture in self.SUPPORTED_ARCHITECTURES}

    async def _detect_architecture(self, model_id: str) -> str:
        entry = await self.registry.get(model_id)
        architecture = entry.architecture if entry is not None else None
        if architecture not in self.SUPPORTED_ARCHITECTURES:
            raise UnsupportedArchitectureError(architecture or "unknown", list(self.SUPPORTED_ARCHITECTURES))
        return architecture

    async def _compile_circuit(self, model_arch: str, model_id: str = "") -> CompiledCircuit:
        if model_arch not in self.SUPPORTED_ARCHITECTURES:
            raise UnsupportedArchitectureError(model_arch, list(self.SUPPORTED_ARCHITECTURES))
        cached = self._circuit_cache.get(model_arch)
        if cached is not None:
            return cached

        async with self._circuit_locks[model_arch]:
            cached = self._circuit_cache.get(model_arch)
            if cached is not None:
                return cached
            self.proving_key_store.mkdir(parents=True, exist_ok=True)
            circuit = CompiledCircuit(
                architecture=model_arch,
                verification_key_ref=str(self.proving_key_store / f"{model_arch}.vk"),
            )
            self._circuit_cache[model_arch] = circuit
            return circuit

    async def generate_proof(self, receipt: ExecutionReceipt, input_data: bytes | None = None) -> ProofArtifact:
        """Generate a bounded proof artifact for a previously executed receipt.

        The EZKL backend proves the circuit on the input itself, so it needs ``input_data``,
        the bytes the TEE ran on. The development backend commits to the receipt alone.
        """
        return await asyncio.wait_for(self._generate_proof(receipt, input_data), timeout=self.timeout_s)

    async def _generate_proof(self, receipt: ExecutionReceipt, input_data: bytes | None) -> ProofArtifact:
        if self.backend == EZKL_PROOF_BACKEND:
            return await self._generate_ezkl_proof(receipt, input_data)
        architecture = await self._detect_architecture(receipt.model_id)
        circuit = await self._compile_circuit(architecture, receipt.model_id)
        entry = await self.registry.get(receipt.model_id)
        if entry is None:
            raise UnsupportedArchitectureError("unknown", list(self.SUPPORTED_ARCHITECTURES))

        public_inputs = {
            "input_hash": receipt.input_hash,
            "output_hash": receipt.output_hash,
            "model_hash": entry.hash,
            "architecture": architecture,
        }
        proof_bytes = commitment_proof(public_inputs, architecture)
        return ProofArtifact(
            proof_bytes=proof_bytes,
            public_inputs=public_inputs,
            verification_key_ref=circuit.verification_key_ref,
            backend=DEVELOPMENT_PROOF_BACKEND,
        )

    async def _generate_ezkl_proof(self, receipt: ExecutionReceipt, input_data: bytes | None) -> ProofArtifact:
        if input_data is None:
            raise ValueError("the EZKL backend needs the input the TEE ran on")
        if hashlib.sha256(input_data).hexdigest() != receipt.input_hash:
            raise ValueError("input does not match the receipt's input hash")
        architecture = await self._detect_architecture(receipt.model_id)
        entry = await self.registry.get(receipt.model_id)
        if entry is None:
            raise UnsupportedArchitectureError("unknown", list(self.SUPPORTED_ARCHITECTURES))
        bundle = ezkl_backend.load_bundle(self.proving_key_store, entry.hash)
        tensor = decode_tensor(input_data)
        proof = await ezkl_backend.prove_async(bundle, tensor)

        tee_output = decode_tensor(receipt.output)
        ezkl_backend.check_output_consistency(proof.outputs, tee_output, bundle.quantization_scale)
        public_inputs = {
            "input_hash": receipt.input_hash,
            "output_hash": receipt.output_hash,
            "model_hash": entry.hash,
            "architecture": architecture,
            "zk_instances_hash": ezkl_backend.instances_hash(proof.proof_bytes),
        }
        return ProofArtifact(
            proof_bytes=proof.proof_bytes,
            public_inputs=public_inputs,
            verification_key_ref=str(bundle.vk_path),
            backend=EZKL_PROOF_BACKEND,
        )

    def verify_proof(self, artifact: ProofArtifact) -> bool:
        """Verify an artifact made by this prover's own backend, for tests and tooling."""
        architecture = artifact.public_inputs.get("architecture")
        if artifact.backend == EZKL_PROOF_BACKEND:
            bundle = ezkl_backend.load_bundle(self.proving_key_store, artifact.public_inputs["model_hash"])
            return ezkl_backend.instances_hash(artifact.proof_bytes) == artifact.public_inputs.get(
                "zk_instances_hash"
            ) and ezkl_backend.verify(bundle, artifact.proof_bytes)
        if architecture not in self.SUPPORTED_ARCHITECTURES:
            return False
        return hmac.compare_digest(artifact.proof_bytes, commitment_proof(artifact.public_inputs, architecture))
