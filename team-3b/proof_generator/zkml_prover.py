"""Architecture-aware proof generation boundary.

The default backend is an explicitly non-production commitment backend. It
keeps the API and privacy boundary testable until the team standardizes on a
real zkML toolchain such as EZKL. It must not be described as a zero-knowledge
proof or used for on-chain verification.
"""

import asyncio
import hmac
from dataclasses import dataclass
from pathlib import Path

from common.constants import SUPPORTED_ARCHITECTURES
from common.exceptions import UnsupportedArchitectureError
from common.model_registry import ModelRegistry
from common.models import ExecutionReceipt, ProofArtifact
from proof_generator.verifier import commitment_proof


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
    ):
        self.registry = registry
        self.proving_key_store = Path(proving_key_store)
        self.timeout_s = timeout_s
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

    async def generate_proof(self, receipt: ExecutionReceipt) -> ProofArtifact:
        """Generate a bounded proof artifact for a previously executed receipt."""
        return await asyncio.wait_for(self._generate_proof(receipt), timeout=self.timeout_s)

    async def _generate_proof(self, receipt: ExecutionReceipt) -> ProofArtifact:
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
        )

    def verify_proof(self, artifact: ProofArtifact) -> bool:
        """Verify a development artifact; real backends must replace this method."""
        architecture = artifact.public_inputs.get("architecture")
        if architecture not in self.SUPPORTED_ARCHITECTURES:
            return False
        return hmac.compare_digest(artifact.proof_bytes, commitment_proof(artifact.public_inputs, architecture))
