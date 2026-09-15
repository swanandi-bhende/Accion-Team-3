# Roadmap & Known Gaps

## Implementation Waves
- **Wave 1:** Project scaffolding, shared contracts, configuration, and documentation.
- **Wave 2:** Model Registry implementation (Redis-backed, IPFS/0G integration).
- **Wave 3:** TEE Runtime simulation and hardware attestation logic.
- **Wave 4:** Inference Engine setup and routing.
- **Wave 5:** Checkpoint 1 (Integration tests for off-chain services).
- **Wave 6:** zkML Proof Generator implementation.
- **Wave 7:** Proof Submitter (batching, DLQ, on-chain submission).
- **Wave 8:** Event Listener (blockchain monitoring, reconnect backoff).
- **Wave 9:** Checkpoint 2 (End-to-end inference flow testing).
- **Wave 10:** Federated Learning Orchestrator (Flower integration).
- **Wave 11:** Verification Interface (API for external verifiers).
- **Wave 12:** Pipeline wiring and Docker composition.
- **Wave 13:** Final Checkpoint and Production Readiness.

## Known Gaps / Blocked Items
The following items are currently stubbed/mocked and are blocked pending deliverables from other teams:
- **Team A (Smart Contracts):** Need real addresses, ABIs, and network RPC endpoints for `TaskManager` and `Escrow` contracts.
- **Team C (Agent Interface):** Need exact `InferenceRequest` payload schema and wallet interaction details.
