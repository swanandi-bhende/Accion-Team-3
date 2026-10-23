# Verifiable AI Execution (Team 3b)

This subsystem runs AI models off-chain and produces proof that the result is correct. A smart
contract can then check that proof, so nobody has to trust the AI agent that ran the model.

It has four parts:

- **Inference.** Runs a model inside a sandboxed process, and returns a signed receipt.
- **Attestation.** A TEE-style report that binds the input, the output, and the enclave.
- **Proofs.** A zero-knowledge proof (zkML) that the model produced the output.
- **Settlement.** Listens for tasks on the chain, and submits proofs back to the contract.

Plus a federated-learning pipeline that trains models across several clients, and a verification
API that checks proofs for anyone.

## Status

The code is built and tested. Several parts are not real yet:

- **The TEE is a software simulator.** Nothing here is hardware-attested.
- **Large zkML models are not feasible yet.** EZKL works up to about 1 million weights. A 500 MB
  model is out of reach on this hardware.
- **Two decisions are open.** What starts a task, and the proof-size target.

Details, numbers and evidence are in [docs/STATUS.md](docs/STATUS.md).

## Quick start

```bash
./setup.sh                      # Python venv, dependencies, a .env copied from .env.example
docker compose up -d            # Redis, IPFS, a local chain, and every service
pytest tests/unit tests/property -q
```

Full instructions are in [docs/SETUP.md](docs/SETUP.md).

## Documentation

| File | What it covers |
| --- | --- |
| [docs/SETUP.md](docs/SETUP.md) | Install, configure, run, test, and the operator scripts |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | The parts, how they connect, and where state lives |
| [docs/API.md](docs/API.md) | HTTP endpoints, authorization, and error codes |
| [docs/INTEGRATION.md](docs/INTEGRATION.md) | What we depend on from Team A and 0G, and what Team C sends us |
| [docs/STATUS.md](docs/STATUS.md) | Requirement by requirement status, measurements, limits, and open decisions |

## Repository layout

| Folder | Contents |
| --- | --- |
| `src/common/` | Shared models, configuration, the model registry, and storage backends |
| `src/inference/` | Inference engine, sandbox, TEE runtime, job API, and the pipeline |
| `src/proof_generator/` | zkML provers: the development backend and EZKL |
| `src/proof_submitter/` | Batches proofs, submits them on chain, and keeps dead letters and alerts |
| `src/event_listener/` | Watches Team A's contracts for tasks |
| `src/verification/` | Verifies attestations and zkML proofs (the `/verify` API) |
| `src/federated_learning/` | Flower-based training and model storage |
| `scripts/` | Operator and benchmark scripts |
| `tests/` | Unit, property, and integration tests |
| `chain/`, `deployments/`, `team_a_assets/` | Local chain, Team A's deployment record, and their ABIs |
| `docker/`, `docker-compose.yml` | Container images and the service stack |
