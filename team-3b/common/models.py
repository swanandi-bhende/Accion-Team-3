"""Common models and types for the Verifiable AI Execution subsystem."""

from pydantic import BaseModel
from typing import Optional

class InferenceRequest(BaseModel):
    model_id: str
    input_data: bytes
    task_id: Optional[str] = None

class ExecutionReceipt(BaseModel):
    execution_id: str
    model_id: str
    model_cid: str
    input_hash: str
    output_hash: str
    output: bytes
    timestamp: float

class AttestationReport(BaseModel):
    enclave_measurement: str
    signer_identity: str
    input_hash: str
    output_hash: str
    hardware_signature: bytes
    timestamp: float

class ProofArtifact(BaseModel):
    proof_bytes: bytes
    public_inputs: dict
    verification_key_ref: str

class ModelEntry(BaseModel):
    model_id: str
    version: int
    cid: str
    hash: str
    binary: Optional[bytes] = None

class FLTaskConfig(BaseModel):
    task_id: str
    num_clients: int
    num_rounds: int
    aggregation_strategy: str = "fedavg"
    round_timeout: float = 300.0

class RoundProvenance(BaseModel):
    round_number: int
    participating_clients: int
    aggregation_strategy: str
    round_duration: float
    resulting_model_hash: str
