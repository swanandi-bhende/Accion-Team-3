# Roadmap

The subsystem is built as three largely independent tracks, each with its own
verification checkpoint, before they are wired together into one pipeline.

## Checkpoints

| Checkpoint | Scope | Status |
| --- | --- | --- |
| `checkpoint-1-core-loop` | Inference loop: TEE runtime, Model Registry, verification interface (Properties 1-5, 19-24) | Implemented and verified |
| `checkpoint-2-onchain-pipeline` | Event listener, proof submitter, mock chain, DLQ (Properties 12-18) | Implemented and verified |
| `checkpoint-3-federated-learning` | FL orchestrator, aggregation strategies, provenance (Properties 9-11) | Implemented and verified |

Checkpoint details, measured numbers, and honest limitations live in
[docs/CHECKPOINTS.md](CHECKPOINTS.md).

## Where the tracks stand

**Core inference loop.** The Inference Engine loads a model from the Model
Registry, verifies its bytes inside the TEE runtime, and returns a receipt with
input/output hashes; a verification API validates attestations. The TEE is a
simulator, not hardware-backed SGX/SEV, and that is stated explicitly wherever
it matters.

**On-chain pipeline.** The event listener consumes `TaskCreated`/`TaskFunded`,
the proof submitter batches proof submissions, and a mock Hardhat chain provides
the contracts. The contracts are still Team A mocks, and real addresses, ABIs,
and finality policy are outstanding dependencies.

**Federated learning.** The orchestrator trains through Flower with FedAvg by
default, records per-round provenance incrementally in Redis, and stores the
trained model through the Model Registry so the Inference Engine can load it.
Clients are simulated locally; a real gRPC path exists and is exercised in
tests, while multi-host SuperLink deployment remains unvalidated. There is no
cryptographic secure aggregation.

## Next: wiring the pipeline together (Step 16)

Step 16 onward integrates the three tracks into one coherent path -- a
federated-trained model flowing into on-chain settlement with a verifiable
proof -- rather than each subsystem being validated in isolation. That work
depends on Team A providing real contract addresses and ABIs; until then the
integration runs against mocks.

## Outstanding dependencies and risks

- Real `TaskManager`/`Escrow` addresses, ABIs, and signer/network configuration
  from Team A.
- Hardware-backed TEE attestation rather than the simulator.
- Production zkML backend selection and proof-time budgets for real model sizes.
- A SuperLink deployment validated in staging, plus TLS, client authentication,
  and cryptographic secure aggregation for the federated track.
