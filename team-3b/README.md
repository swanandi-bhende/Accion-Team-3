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
- **Team A (Smart Contracts):** INTEGRATED. The listener and submitter run against Team A's real contracts — ABIs in `team_a_assets/`, addresses in `deployments/galileo.json`, and their deployed bytecode replayed onto the local `chain/` node by `scripts/ops/install_team_a_local.py`. Chain id 16602, the same as 0G Galileo.
- **Team C (Agent Logic):** MOCKED. Expected agent payloads are stubbed locally pending finalized schemas from Team C.

### Do not use the mock contracts

Our own stub contracts are archived in `mocks/legacy_mock_contracts/`. **They are
retired: do not deploy them, do not point a service at them, and do not test
against them.** They mirror Team A's ABI shape but none of their authorisation or
lifecycle rules (Prover allow-list, bids, escrow release), so a passing test there
proves almost nothing. Every chain-facing test in this repository now installs
Team A's real bytecode at their Galileo addresses instead — see `mocks/README.md`.

## Quick Start
1. **Clone the repository.**
2. **Install dependencies:** Run `./setup.sh` (Requires Python 3.10+ and Docker).
3. **Configure Environment:** Copy `.env.example` to `.env` and fill in necessary fields.
4. **Run Tests:** `pytest tests/unit/ tests/property/ --tb=short -v` (the chain tests start a local Hardhat node running Team A's contracts; Node 20+ is needed)
5. **Start Services:** `docker-compose up -d`

## Core Loop Checkpoint

The V1.6 checkpoint covers Properties 1-5, 19, and 20 with Hypothesis, focused
unit tests, and a CI gate backed by a Redis service container. Run
`pytest --cov=inference --cov=common --cov-report=term-missing` to reproduce the
coverage report. The review record and known limitations are in
`docs/CHECKPOINTS.md`.

Reproduce the real EZKL benchmark with `venv/bin/python
scripts/zkml/ezkl_mlp_benchmark.py`. The measured result is written to
`data/zkml_spike/benchmark_result.json`.

## Further Reading
- **Requirements & Architecture:** See the design document in `/docs`.
- **Known Gaps:** Refer to `ROADMAP.md` for items blocked on other teams.
