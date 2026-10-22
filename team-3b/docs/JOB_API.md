# Job API (Team 3B)

Asynchronous inference jobs for Team 3C's execution adapter. A job runs inference, the TEE
attestation and the zkML proof for one input, and keeps the result as a receipt.

`POST /inference` is still there. It returns only the execution receipt, with no attestation
and no proof, so use the job API for work that needs a verifiable result.

## Endpoints

### `POST /jobs`

Records the job and returns at once. The work runs in the background.

Request body:

| Field | Type | Notes |
| --- | --- | --- |
| `task_id` | string | Team A task id: 32-byte hex, with or without `0x` |
| `model_id` | string | Model Registry id to run. Give this or `model_cid`, not both |
| `model_cid` | string | CID of the model's current version. Give this or `model_id`, not both |
| `input_hash` | string | SHA-256 hex of the raw input bytes |
| `input_data` | string | Base64 of the `.npy` input tensor |
| `authorization` | object | `{wallet_address, nonce, expires_at, signature}`, from the challenge below |

Authorization. A job is accepted only from the worker Team A's `TaskManager` records for the
task:

1. `POST /challenges` with `{"task_id", "wallet_address"}`. It returns `{nonce, expires_at,
   message}` (status 201). The nonce lives 120 s, and the message names the task, the wallet,
   the nonce and the expiry.
2. The wallet signs `message` with EIP-191 `personal_sign`, and sends `signature` in
   `authorization`, with the same `nonce` and `expires_at`.
3. The service consumes the nonce, so each challenge gives one attempt. It checks that the
   nonce is live, that the signature recovers to the wallet, and that `taskWorker(task_id)` is
   that wallet.

Authorization errors:

- `401 challenge_unknown`: the nonce is unknown, expired, or already used.
- `401 challenge_mismatch`: the authorization does not match its challenge's task or wallet.
- `401 challenge_expired`: `expires_at` has passed.
- `401 bad_signature`: the signature is malformed, or it does not recover to the wallet.
- `403 not_task_worker`: the wallet is not the task's recorded worker.
- `503 auth_unavailable`: no chain is configured, so the worker cannot be checked. The service
  refuses jobs rather than accept them unchecked.

Responses:

- `202 Accepted`, with `Location: /jobs/{job_id}`:
  `{"job_id", "status", "task_id", "input_hash", "model_id", "deadline", "created"}`.
  `created` is `false` when the job already existed.
- `404 ModelNotFound`: no model's current version is stored at `model_cid`. A CID that was
  replaced by a newer version of its model does not resolve.
- `422 InvalidJob`: `input_hash` does not match `input_data`, or `input_data` is not an
  `.npy` tensor. Schema errors (bad `task_id`, bad base64, both or neither model field)
  also return 422 from the schema.
- `409 JobConflict`: the same `(task_id, input_hash)` was posted for a different `model_id`.

### `GET /jobs/{job_id}`

Reading needs a signed challenge from the job's task worker, the same as posting. Get one from
`POST /challenges` with the job's `task_id` and the worker's wallet, sign its `message`, and send
the result in headers:

- `X-Wallet-Address`: the wallet
- `X-Challenge-Nonce`, `X-Challenge-Expires-At`, `X-Challenge-Signature`: from the challenge

Each read takes a fresh challenge, since a challenge is single-use. The worker's agent can sign
these programmatically, so a polling loop needs one challenge and one signature per poll.

- `200`: the job. Once it is `completed`, `receipt` is set.
- `401 challenge_required`: a header is missing or malformed. The other authorization errors
  are as for `POST /jobs`.
- `403 not_task_worker`: the wallet is not the task's worker.
- `404 JobNotFound`: no such job. This is checked before the challenge.

Job fields: `job_id`, `status`, `task_id`, `input_hash`, `model_id`, `created_at`,
`updated_at`, `deadline`, `error`, `receipt`.

Polling: while a job is `queued` or `running`, `GET` sends `Retry-After: 5`. Poll until the
job is `completed` or `failed`, or until `deadline` (Unix seconds) passes. Team 3C's retry
plan (2–60 s backoff) applies to `POST /jobs` and `GET` alike. `POST` is idempotent, so a
retried post never starts a second job.

## Timeouts

A job's budget is 150 s: the inference budget of 30 s (Requirement 1.1) plus the proof budget
of 120 s (Requirement 3.5). `deadline` is `created_at` plus that budget. It is absolute, so
a job resumed after a restart keeps its original deadline. The 10 s proof batch window and
transaction confirmation do not apply here, since this route does not settle on chain.

A job still running at its deadline fails with `job_timeout`, and its work is cancelled.

## Lifecycle

`queued` → `running` → `completed` or `failed`.

- `completed`: `error` is null and `receipt` is set.
- `failed`: `receipt` is null and `error` is `{"code", "detail"}`.

Error codes: `model_not_found`, `inference_timeout`, `job_timeout`, `integrity_failure`,
`tee_unavailable`, `model_execution_failed`, `invalid_input`, `proof_keys_missing`,
`proof_inconsistent`, `unsupported_architecture`, `proof_failed`, `proof_timeout`,
`internal_error`.

## Idempotency

A job is keyed by `(task_id, input_hash)`. Posting the same key again returns the existing
job, and the work runs once. The key is claimed atomically in Redis, so racing posts still
create one job.

## Receipt

```json
{
  "execution": { "execution_id", "model_id", "model_cid", "input_hash", "output_hash", "output", "timestamp" },
  "attestation": { "report": { "enclave_measurement", "signer_identity", "input_hash", "output_hash", "hardware_signature", "timestamp" },
                   "signer_public_key": "<base64>" },
  "zkml_proof": { "proof_bytes": "<base64>", "public_inputs": { "input_hash", "output_hash", "model_hash", "architecture" },
                  "verification_key_ref", "backend" },
  "settlement": { "status": "submitted", "tx_hash" } | { "status": "not_submitted", "reason" }
                | { "status": "dead_lettered" } | { "status": "failed", "reason" } | { "status": "disabled" }
}
```

`output` is base64 of the `.npy` output tensor. The attestation and the proof use the wire
formats of `POST /verify`, so a receipt can be checked with the verification API.

Settlement submits the proof and the model CID to Team A's `TaskManager.submitProofs`, and
`tx_hash` is the transaction. The job completes whatever the settlement outcome, since the
receipt is valid without it:

- `not_submitted`: the proof's backend is not allowed on chain. This is the case for the
  development backend, which is the default.
- `dead_lettered`: the batch failed its retries and went to the dead-letter queue.
- `failed`: the submission raised. The `reason` says why.
- `disabled`: no signing key or chain is configured for this service.

The chain is not contacted at start-up, so an unreachable RPC does not stop the service.

## Durability

Job state is kept in Redis (`jobs:job:*`, `jobs:by_key:*`, `jobs:pending`). The input is
kept until the job finishes, then deleted. On start-up, unfinished jobs run again. A job
interrupted mid-run starts over, so each stage must be safe to repeat. Inference and proof
are, since each run produces a new execution.

## Not done yet

These are agreed in principle with Team 3C but not built:

- **Trigger.** Company spec Requirement 5 starts workloads on `TaskCreated` and `TaskFunded`,
  which is what the listener does. Team 3C proposes `BidAccepted` instead. The two conflict,
  so this is for the company and Team 3C to decide. Jobs here come only from `POST /jobs`.
- **Input transport.** The input comes inline as base64. Fetching it by CID is out of the
  spec's scope, so it is not planned.
- **On-chain settlement on this Mac.** It needs the EZKL backend, and ezkl has no arm64 wheel,
  so a job here always reports `not_submitted`. Settlement is built and tested with a stand-in.
