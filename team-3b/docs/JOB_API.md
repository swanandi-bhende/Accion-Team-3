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
| `model_id` | string | Model Registry id to run |
| `input_hash` | string | SHA-256 hex of the raw input bytes |
| `input_data` | string | Base64 of the `.npy` input tensor |

Responses:

- `202 Accepted`, with `Location: /jobs/{job_id}`:
  `{"job_id", "status", "task_id", "input_hash", "model_id", "created"}`.
  `created` is `false` when the job already existed.
- `422 InvalidJob`: `input_hash` does not match `input_data`, or `input_data` is not an
  `.npy` tensor. Schema errors (bad `task_id`, bad base64) also return 422 from the schema.
- `409 JobConflict`: the same `(task_id, input_hash)` was posted for a different `model_id`.

### `GET /jobs/{job_id}`

- `200`: the job. Once it is `completed`, `receipt` is set.
- `404 JobNotFound`.

Job fields: `job_id`, `status`, `task_id`, `input_hash`, `model_id`, `created_at`,
`updated_at`, `error`, `receipt`.

## Lifecycle

`queued` → `running` → `completed` or `failed`.

- `completed`: `error` is null and `receipt` is set.
- `failed`: `receipt` is null and `error` is `{"code", "detail"}`.

Error codes: `model_not_found`, `inference_timeout`, `integrity_failure`, `tee_unavailable`,
`model_execution_failed`, `invalid_input`, `proof_keys_missing`, `proof_inconsistent`,
`unsupported_architecture`, `proof_failed`, `proof_timeout`, `internal_error`.

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
                  "verification_key_ref", "backend" }
}
```

`output` is base64 of the `.npy` output tensor. The attestation and the proof use the wire
formats of `POST /verify`, so a receipt can be checked with the verification API.

## Durability

Job state is kept in Redis (`jobs:job:*`, `jobs:by_key:*`, `jobs:pending`). The input is
kept until the job finishes, then deleted. On start-up, unfinished jobs run again. A job
interrupted mid-run starts over, so each stage must be safe to repeat. Inference and proof
are, since each run produces a new execution.

## Not done yet

These are agreed in principle with Team 3C but not built:

- **Authentication.** Wallet-signed challenge-response. The routes are open today.
- **Trigger.** Execution should start on Team A's `BidAccepted` event, with the worker taken
  from `taskWorker(task_id)`. Today jobs are created only by `POST /jobs`.
- **Model mapping.** `model_id` is a registry id. Team 3C's payload carries a model CID, and
  the lookup from CID to registry entry is not built.
- **Input transport.** The input comes inline as base64. Fetching it by CID is not built.
- **Settlement.** Receipts are not submitted on chain from this route.
