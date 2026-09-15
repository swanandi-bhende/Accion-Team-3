# Verifiable AI Execution Subsystem (Team 3b)

Verifiable AI Execution subsystem (Team 3b) — off-chain AI inference with TEE-attested compute isolation and zkML proof generation, feeding cryptographic proofs back to the on-chain settlement layer.

## Problem Statement
The integration of Artificial Intelligence with Web3 faces a critical vulnerability known as the "Oracle Problem": smart contracts cannot natively trust off-chain AI outputs, as standard centralized servers offer no guarantees of computational integrity. This subsystem exists to make inference verifiable. By utilizing Trusted Execution Environments (TEEs) for hardware attestation and zero-knowledge machine learning (zkML) proofs, we ensure that an AI agent hasn't spoofed data, altered models, or tampered with execution to claim bounties. 

## Core Responsibilities
- **Blockchain Event Monitoring:** Listening to Team 3a's on-chain events (`TaskCreated`, `TaskFunded`) to trigger workloads.
- **Secure Execution:** Executing inference inside a TEE (Intel SGX or AMD SEV) for memory isolation and encryption.
- **Proof Generation:** Generating zero-knowledge (zkML) proofs that attest to correct execution without revealing model weights.
- **Federated Learning:** Orchestrating federated learning pipelines across nodes via the Flower framework.
- **On-chain Settlement:** Submitting verifiable cryptographic proofs and execution artifacts (CIDs) back to the blockchain.

## System Architecture Overview
The system workflow follows a strict pipeline:
`Event_Listener` → `Inference_Engine` → `TEE_Runtime` → `Proof_Generator (zkML)` → `Proof_Submitter` → `on-chain`

### Integration Status
- **Team A (Smart Contracts):** MOCKED. Currently utilizing stub ABIs and events in `/mocks/contracts/` until the real task manager and escrow contracts are shipped.
- **Team C (Agent Logic):** MOCKED. Expected agent payloads are stubbed locally pending finalized schemas from Team C.

## Quick Start
1. **Clone the repository.**
2. **Install dependencies:** Run `./setup.sh` (Requires Python 3.10+ and Docker).
3. **Configure Environment:** Copy `.env.example` to `.env` and fill in necessary fields.
4. **Run Tests:** `pytest tests/unit/ --tb=short`
5. **Start Services:** `docker-compose up -d`

## Further Reading
- **Requirements & Architecture:** See the design document in `/docs`.
- **Known Gaps:** Refer to `ROADMAP.md` for items blocked on other teams.
