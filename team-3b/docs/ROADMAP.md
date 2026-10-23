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
- Hardware-backed TEE (Requirements 2 and 8). Today's simulator has an in-memory attestation
  key, a constant measurement, a plaintext sealed key, no real secure erase, and no hardware
  test in CI. Closing it needs a Linux SGX or SEV host and an attestation service. It cannot be
  closed on macOS. See the limit in `docs/CHECKPOINTS.md`.
- Sealed models: the key is now a file (`TEE_SEALED_KEY_PATH`), but no production path seals
  models yet. Decide where sealing happens before relying on the key file for persistence.
- zkML at production size. EZKL is measured up to a 1,053,700-weight MLP (91 s proving, 2.0 GB
  proving key). A 500 MB model is not feasible on EZKL with a 120 s budget here. Choosing a
  proving approach for that size is open. Compose settlement needs a linux/amd64 host, because
  the EZKL wheel is missing for linux/arm64 containers. That host has not been used yet.
- **Storage: IPFS by default, 0G Storage behind `MODEL_STORAGE_BACKEND=0g`.** The 0G
  adapter (`common/storage/zerog_backend.py`) runs the official `0g-storage-client` CLI,
  which builds the Merkle tree, submits the Flow transaction and uploads the segments. It is
  tested against a stand-in CLI, and it has run once against the Galileo testnet (chain 16602):
  a 155-byte fixture was uploaded, downloaded, and matched byte for byte. Mainnet has not been
  used. The CLI is built into `docker/base.Dockerfile` at a pinned commit, so every service
  image has it. A wallet short of gas is refused with `ConfigurationError` rather than retried.
  Requirement 7 names IPFS, Filecoin and 0G as options, so IPFS stays the default.
- **Dead-letter alerts (Requirement 6.5).** A proof that is dead-lettered, whether after failed
  retries or rejected on chain, raises an alert. It is logged, recorded in Redis
  (`proof_submitter:alerts`) with its delivery result, and posted to `ALERT_WEBHOOK_URL` when
  that is set. No channel is configured yet, so alerts currently stay in Redis and the logs.
- A SuperLink deployment validated in staging, plus TLS, client authentication,
  and cryptographic secure aggregation for the federated track.
