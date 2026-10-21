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
the proof submitter batches proof submissions, and the local chain runs Team A's
deployed bytecode at their Galileo addresses. The contracts are real; RPC
endpoints, a funded allow-listed signer, and the finality policy are still
outstanding from Team A.

**Federated learning.** The orchestrator trains through Flower with FedAvg by
default, records per-round provenance incrementally in Redis, and stores the
trained model through the Model Registry so the Inference Engine can load it.
Clients are simulated locally; a real gRPC path exists and is exercised in
tests, while multi-host SuperLink deployment remains unvalidated. There is no
cryptographic secure aggregation.

## Next: wiring the pipeline together (Step 16)

Step 16 onward integrates the three tracks into one coherent path -- a
federated-trained model flowing into on-chain settlement with a verifiable
proof -- rather than each subsystem being validated in isolation. Team A's
addresses and ABIs are in hand, so that work runs against their contracts; the
remaining dependency is a funded prover on Galileo and a decision on who accepts
the bid that settlement pays.

## Outstanding dependencies and risks

- A published Galileo RPC endpoint and a funded, allow-listed signer from Team A
  (addresses and ABIs are delivered).
- A bid/worker policy: `settleTask` pays `taskWorker`, so proofs on a task nobody
  bid on are recorded but unpayable (see `docs/INTEGRATION_CONTRACTS.md` §6).
- Hardware-backed TEE attestation rather than the simulator.
- Production zkML backend selection and proof-time budgets for real model sizes.
- A SuperLink deployment validated in staging, plus TLS, client authentication,
  and cryptographic secure aggregation for the federated track.
