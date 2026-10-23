# API

Two HTTP services. Both speak JSON.

- **Inference API**, port 8000: runs models, and takes jobs from agents.
- **Verification API**, port 8001: checks proofs.

## Inference API

### Authorization

Only the worker that Team A's contract records for a task may run its model. The worker proves
it by signing a challenge with its wallet:

1. `POST /challenges` with `{"task_id": "...", "wallet_address": "0x..."}`. The reply has a
   `nonce`, an `expires_at` and a `message`. Status 201.
2. The wallet signs `message` with EIP-191 `personal_sign`.
3. Send the signature with the request, as `authorization`, or in headers for reads.

Each challenge works once and lasts 120 s. The service checks that the nonce is live, that the
signature recovers to the wallet, and that `taskWorker(task_id)` on the chain is that wallet.

| Error | Meaning |
| --- | --- |
| `401 challenge_unknown` | The nonce is unknown, expired, or already used |
| `401 challenge_mismatch` | The request does not match its challenge |
| `401 challenge_expired` | `expires_at` has passed |
| `401 bad_signature` | The signature is malformed or does not match the wallet |
| `403 not_task_worker` | The wallet is not the task's worker |
| `503 auth_unavailable` | No chain is configured, so the worker cannot be checked |

### `POST /inference`

Runs one model and returns its receipt. Use this for a quick answer. Use `/jobs` for work that
needs an attestation and a proof.

```json
{
  "task_id": "0x…64 hex…",
  "model_id": "toy_linear",
  "input_data": "<base64 of an .npy tensor>",
  "authorization": {"wallet_address": "0x…", "nonce": "…", "expires_at": 0, "signature": "0x…"}
}
```

Give `model_id` (the registry id) or `model_cid` (the CID of the model's current version), not both.
A CID that is no longer the current version returns 404.

Returns the execution receipt: `execution_id`, `model_id`, `model_cid`, `input_hash`,
`output_hash`, `output` (base64 of the output tensor) and `timestamp`. It does not echo `task_id`.

| Status | Error | Meaning |
| --- | --- | --- |
| 404 | `ModelNotFound` | No such model, or no current model at that CID |
| 408 | `TimeoutExceeded` | The run took longer than `TEE_INFERENCE_TIMEOUT_S` (30 s) |
| 422 | `ModelExecutionFailed` | The model cannot run on this input |
| 422 | `IntegrityCheckFailed` | The model bytes do not match their registered hash |
| 503 | `TEEUnavailable` | The enclave is not available |

### `POST /jobs`

Accepts a job and returns at once (202, with `Location: /jobs/<id>`). The body is the same as
`/inference`, plus `input_hash` (SHA-256 of the raw input). The work runs in the background and
produces an attestation, a zkML proof, and a settlement record.

- The same `(task_id, input_hash)` returns the same job, so retries are safe. The same key for a
  different model returns 409 `JobConflict`.
- A job's budget is 150 s. A job still running at its deadline fails with `job_timeout`.

### `GET /jobs/{job_id}`

Returns the job. Reading needs a fresh challenge, sent in headers:
`X-Wallet-Address`, `X-Challenge-Nonce`, `X-Challenge-Expires-At` and `X-Challenge-Signature`.
While the job is `queued` or `running`, the reply carries `Retry-After: 5`.

Statuses: `queued` → `running` → `completed` or `failed`. A failed job has `error: {code, detail}`.
A completed job has `receipt`, which holds `execution`, `attestation`, `zkml_proof`, and
`settlement`. Settlement is `pending` until the proof is submitted, then one of `submitted`,
`not_submitted`, `dead_lettered`, `failed` or `disabled`.

### `POST /challenges` and `GET /health`

`POST /challenges` is described under Authorization. `GET /health` returns `{"status": "ok"}`.

## Verification API

### `POST /verify`

```json
{
  "proof_format": "tee_attestation",
  "public_inputs": {"input_hash": "…", "output_hash": "…"},
  "proof_bytes": "<the proof document as a JSON string>"
}
```

`proof_format` is `tee_attestation` or `zkml_snark`. Send the proof either inline in
`proof_bytes` or by `proof_reference` (a `Qm…` or `bafy…` CID, or a `zg:` 0G CID). Inline bytes are
the proof's JSON text, so they are sent as a plain string.

```json
{"result": true, "timestamp": 1735689600.0}
{"result": false, "reason_code": "expired_attestation", "timestamp": 1735689600.0}
```

| `reason_code` | Meaning |
| --- | --- |
| `invalid_signature` | The signature does not verify, or the signer is not trusted |
| `expired_attestation` | The attestation is older than the allowed window |
| `proof_mismatch` | The proof does not match the public inputs |
| `malformed_proof` | The proof cannot be parsed, or no proof was supplied |
| `verification_timeout` | Verification took longer than 5 s (status 408) |

An unknown `proof_format` returns 400, with the list of formats the verifier supports.
The verifier never answers `result: true` for a proof it cannot check.

### `GET /health`

Returns `{"status": "ok"}`.

## Receipt format

The receipt for a completed job has this shape. Each part can be checked with the verification API.

```json
{
  "execution": {"execution_id", "model_id", "model_cid", "input_hash", "output_hash", "output", "timestamp"},
  "attestation": {"report": {…}, "signer_public_key": "<base64>"},
  "zkml_proof": {"proof_bytes": "<base64>", "public_inputs": {…}, "verification_key_ref", "backend"},
  "settlement": {"status": "pending | submitted | not_submitted | dead_lettered | failed | disabled"}
}
```
