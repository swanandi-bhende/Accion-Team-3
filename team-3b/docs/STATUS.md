# Status

What is built, what is measured, what is not real yet, and what is still open. Each status below
names the test or file that shows it.

Legend: **Met** means built and tested against the spec's bound. **Partly** means built, with a
gap noted. **Not met** means the spec's requirement is not satisfied.

## Requirements

### 1. Inference

| Req | Status | Evidence and gap |
| --- | --- | --- |
| 1.1 Run a model within 30 s, up to 500 MB | **Met** | 502 MB model in 2.5–3.0 s (`tests/integration/test_latency_requirements.py`). Real model mixes are not measured. |
| 1.2 Each run in its own process | **Met** | `src/inference/sandbox.py`. The process is isolated, not hardware-isolated. |
| 1.3 Receipt with hashes | **Met** | `src/common/models.py`, `ExecutionReceipt`. |
| 1.4 404 for an unknown model | **Met** | `tests/integration/test_inference_engine_api.py` |
| 1.5 30 s timeout with 408 | **Met** | Same test file |
| 1.6 Audit log | **Met** | Each run is logged to Redis. |

### 2. Hardware TEE

| Req | Status | Evidence and gap |
| --- | --- | --- |
| 2.x Hardware-attested enclave (SGX or SEV) | **Not met** | Every `TEE_MODE` runs one software simulator (`src/inference/enclave_sim.py`). The runtime logs "not hardware-attested". |
| Attestation is hardware-bound | **Not met** | The signing key is generated in memory at start-up, so it changes on every restart. |
| Measurement | **Not met** | A constant string, `simulated_enclave_measurement_v1`. |

Closing this needs a Linux host with SGX or SEV hardware and an attestation service. It cannot be
done on macOS, and no hardware test runs in CI.

### 3. zkML proofs

| Req | Status | Evidence and gap |
| --- | --- | --- |
| 3.1 Proof for each execution | **Met** | EZKL and the development backend. |
| 3.2 Constant-time verification | **Not measured** | Verification time per architecture has not been recorded. |
| 3.3 Proof artifact fields | **Met** | `ProofArtifact` |
| 3.4 Error for unsupported architectures | **Met** | `UnsupportedArchitectureError` |
| 3.5 Proof within 120 s for up to 500 MB | **Not met** | Measured to about 1 million weights: 91 s to prove, 2.0 GB proving key. A 500 MB model is not feasible on EZKL here. |
| 3.6 No model weights leak | **Partly** | Tested for the development backend. The EZKL artifacts have not been inspected. |
| Property 7: constant proof size | **Not met** | EZKL proof size grows with the model (17.6 KB to 218 KB across the ladder). |

**EZKL scale ladder** (`scripts/zkml/ezkl_scale_benchmark.py`, `data/zkml_spike/scale/`). Every proof
verified. Peak memory was measured on a 16 GB arm64 Mac.

| Weights | Setup (s) | Prove (s) | Proof (bytes) | Proving key (bytes) | Peak memory (MB) |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 340 | 16.8 | 16.3 | 20,161 | 553,912,075 | 1,096 |
| 66,820 | 11.7 | 12.9 | 55,580 | 553,912,075 | 1,131 |
| 264,708 | 28.3 | 66.1 | 105,892 | 906,496,987 | 1,532 |
| 593,668 | 34.9 | 37.3 | 155,921 | 1,259,081,899 | 1,618 |
| 1,053,700 | 39.5 | 91.3 | 218,375 | 1,989,417,635 | 2,688 |

Prove times are noisy: one run at 768 weights was faster than one at 512. A 500 MB model is about
125 million weights. Extrapolating from the ladder puts proving at hours and the key at hundreds of
gigabytes. That is an extrapolation, not a measurement.

### 4. Federated learning

| Req | Status | Evidence and gap |
| --- | --- | --- |
| 4.1–4.3 Flower server, rounds, FedAvg | **Met** | `src/federated_learning/`. Clients are simulated in-process. |
| 4.4 Final model stored, CID generated | **Met** | Stored through the model registry. |
| 4.5 Round timeout, aggregate the responsive clients | **Met** | Clients train concurrently under one round deadline (`test_a_round_is_bounded_by_one_timeout_not_one_per_client`). |
| 4.6 Provenance per round | **Met** | Client count, strategy, duration and model hash (`fl:provenance:<task>`). |

Gaps: a multi-host Flower SuperLink deployment has not been validated. There is no secure aggregation.

### 5. Event listener

| Req | Status | Evidence and gap |
| --- | --- | --- |
| 5.1 Detect `TaskCreated` within 15 s | **Met** | 0.52 s on a fake chain (`test_latency_requirements.py`). Block production and RPC latency on Galileo are not included. |
| 5.2 Detect `TaskFunded` within 15 s | **Met** | Same polling path |
| 5.3 Extract the event fields | **Met** | `src/event_listener/listener.py` |
| 5.4 Start work on the chosen trigger | **Partly** | Both triggers are built. `EXECUTION_TRIGGER` picks one. The company has not chosen. |
| 5.5 Reconnect with backoff, 1–60 s | **Met** | `RECONNECT_BASE = 1.0`, `RECONNECT_MAX = 60.0` |
| 5.6 Resume from the last block | **Met** | Progress is kept in Redis. Tests cover a chain reset. |

### 6. Proof submission

| Req | Status | Evidence and gap |
| --- | --- | --- |
| 6.1 Submit within a 10 s batch window | **Met** | `PROOF_BATCH_WINDOW_S` |
| 6.2 Payload with task id, proof hash, model CID and result hash | **Met** | `src/proof_submitter/submitter.py` |
| 6.3 Anchoring record with the transaction hash | **Partly** | A log line (`PROOF_ANCHORED`). It is not an on-chain event. |
| 6.4 Three retries, 10 s apart, then the dead-letter queue | **Met** | `MAX_RETRIES = 3`, `RETRY_INTERVAL = 10.0` |
| 6.5 Alert on dead-lettered proofs | **Partly** | Logged, recorded in `proof_submitter:alerts`, and posted to `ALERT_WEBHOOK_URL` when set. **No channel is configured.** |

### 7. Model registry and storage

| Req | Status | Evidence and gap |
| --- | --- | --- |
| 7.1 Upload the trained model | **Met** | IPFS by default. The 0G backend ran once on Galileo testnet. |
| 7.2 Store the CID | **Met** | `src/common/model_registry.py` |
| 7.3 CID in the proof submission | **Met** | Submitter payload |
| 7.4 Version to CID mapping | **Met** | `model:<id>:v<n>`, `model:cid:<cid>` |
| 7.5 Queue and retry, 5–300 s backoff | **Met** | Spooled and queued before the first attempt. The backoff sequence is tested. |
| 7.6 CID checked before use | **Met** | Inference and job endpoints resolve the CID first. |

### 8. Sealed models

| Req | Status | Evidence and gap |
| --- | --- | --- |
| 8.1–8.2 Decrypt inside the enclave, check integrity | **Partly** | Decryption and the integrity check work, in the simulated enclave. |
| 8.3 Secure erase | **Not met** | `secure_erase` zeroes a copy of the buffer, not the caller's buffer, and it ignores errors. |
| 8.4 Key handling | **Partly** | The key is a file, created with mode 0600 on first use (`TEE_SEALED_KEY_PATH`). It is plaintext on the host. |
| 8.6 Models up to 500 MB | **Met** | Tested with a 502 MB model (1.1). |

No production path seals models yet, so the sealed-model path is exercised only by tests.

### 9. Verification

| Req | Status | Evidence and gap |
| --- | --- | --- |
| 9.1 Verdict within 5 s | **Met** | Under 1 ms for 20 runs of each format. The EZKL verify took 0.08–0.45 s in the ladder. |
| 9.2 Check TEE and zkML proofs | **Met** | `src/verification/` |
| 9.3 Reason codes | **Met** | `invalid_signature`, `expired_attestation`, `proof_mismatch`, `malformed_proof` |
| 9.4 Proof by value or by reference | **Met** | `proof_reference` accepts IPFS and 0G CIDs |
| 9.5 Error for unsupported formats | **Met** | 400, with the supported formats listed |

## Design properties

Properties 1–23 each have at least one test that names them. The property tests use Hypothesis.
Property 7 (constant proof size) is not met, as noted above.

## Measurements

| What | Result | Conditions |
| --- | --- | --- |
| Event detection (5.1) | 0.52 s | Fake chain, 1 s polling |
| Verification (9.1) | under 1 ms | 20 runs per format, development proofs |
| Inference, 502 MB model (1.1) | 2.5–3.0 s | One MatMul, sandboxed process |
| 0G round trip | 155 bytes, byte-identical | Galileo testnet, once |
| EZKL, 1.05 million weights | 91 s prove, 2.0 GB key | 16 GB arm64 Mac |

All measurements come from one machine. They are not production figures.

## Known limits

- The TEE is a simulator. Nothing is hardware-attested.
- Secure erase does not erase the caller's buffer.
- EZKL cannot prove a 500 MB model within 120 s on this hardware.
- The 0G CLI receives the signing key as an argument, so the key is visible in the process list
  during an upload.
- The 0G upload is one attempt per call. A permanent failure other than low balance (for example a
  revert) is retried indefinitely, because the upload queue has no attempt cap.
- Dead-letter alerts are delivered once, with no retry.
- The prover allow-list check runs only in the proof-submitter container, which takes no proof
  traffic. The pipeline does not run it.
- Federated learning uses simulated clients. There is no secure aggregation.
- Chain tests run against a local node that replays Team A's bytecode. No test has placed a real
  bid on Galileo.

## Open decisions

| Decision | Who | Current state |
| --- | --- | --- |
| What starts execution (`TaskCreated`/`TaskFunded` or `BidAccepted`) | Company and Team C | Both built. Default is the spec's. |
| Proof-size target (Property 7) | Spec owner | Option A (at most 64 KiB) proposed. Not accepted. |
| Alert channel for dead letters | Us | Webhook built. No URL configured. |
| Response carries `task_id` (DRIFT-012) | Team C | Open |
| Model mapping (DRIFT-014) | Team A and Team C | Open. Proposal in [INTEGRATION.md](INTEGRATION.md). |
| Input and output format (DRIFT-015) | Team C | Open |
| Prover allow-list on Galileo (DRIFT-006) | Team A | Blocked. Our signer is not on it. |
| Bids on tasks (DRIFT-011) | Team A | Blocked. Settlement cannot pay until someone bids. |

## Next steps

1. Decide the trigger, and get the proof-size decision from the spec owner.
2. Move the prover allow-list check into the pipeline, and retire the idle proof-submitter container.
3. Give dead-letter alerts a channel, and add retries to the webhook.
4. Close DRIFT-012, DRIFT-014 and DRIFT-015 with Team C.
5. Measure a real model at production size, or accept the EZKL limit in writing.
6. Put a hardware TEE behind the enclave interface on a Linux SGX or SEV host.

## History

Checkpoints reviewed so far, each with its tests:

- **Core loop** (V1.6): inference, the TEE runtime simulator, the registry, the verification interface.
- **zkML risk review** (V1.7): the EZKL spike and its key sizes.
- **On-chain pipeline** (V1.11): listener and submitter against Team A's contracts.
- **Federated learning** (V1.15): orchestrator, strategies, provenance.
- **Full pipeline** (V1.16): end to end on the local chain.
- **Close-out** (V1.17): items from the review list, including the round-timeout fix.

Earlier review records are in git history.
