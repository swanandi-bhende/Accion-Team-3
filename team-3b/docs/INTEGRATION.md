# Integration

Who we depend on, what we use from them, and what we still need from them. The asks are in
[What we need](#what-we-need) at the end, with the reason and the blocking status of each.

## Team A (smart contracts)

Team A's contracts run on the 0G Galileo testnet, chain id 16602. Their addresses are in
`deployments/galileo.json` and `.env.example`. Their ABIs are in `team_a_assets/`.

| Contract | Address | What we use it for |
| --- | --- | --- |
| TaskManager | `0xA55f34e4137aD55f48aB268d4361F2225C4B5078` | Task events, `taskWorker`, `submitProofs`, `isProver` |
| EscrowContract | `0x5B685Ca03A8072FB3f9721Dd7017Ed0D8C719217` | `TaskFunded` event |
| SettlementContract | `0x45D1a7AF404D2EDf5E5c23C7c0b6338227b9BC4E` | Not called yet (see the asks) |
| Test ERC-20 (`TEST`) | `0x22374BC6FF12FF2Bf407Da332F16Ee65D52CE0EC` | Not used yet |

For local tests, `scripts/ops/install_team_a_local.py` copies their deployed bytecode onto a local
chain at the same addresses, so the tests run Team A's real rules.

### Events we read

| Event | Contract | Fields | Used for |
| --- | --- | --- | --- |
| `TaskCreated` | TaskManager | `taskId`, `creator`, `descriptionHash`, `deadline`, `bountyAmount` | Starting a task |
| `TaskFunded` | Escrow | `taskId`, `funder`, `amount` | Starting a task |
| `BidAccepted` | TaskManager | `taskId`, `worker` | Starting a task, in `bid_accepted` mode only |
| `ProofRejected` | TaskManager | `taskId`, `reason` | Dead-lettering a rejected proof |

### Functions we call

- `submitProofs(bytes32[], bytes32[], string[], bytes32[])`. Bounded by `MAX_PROOF_BATCH`. Returns
  how many proofs it accepted. A bad entry is rejected on its own with `ProofRejected`.
- `taskWorker(bytes32)`. The worker allowed to run a task. The inference API uses it to check
  signed requests.
- `isProver(address)`. Our signer must be on the prover allow-list. The check runs at startup.

### Agreed

On 2026-10-20 and 2026-10-21: `taskId` is `bytes32`, event field names, `submitProofs` lives on
TaskManager, the submitter signs with its own key, and the mock contracts were retired.

## Team C (agent)

Team C's agent is not integrated. What their code has today:

- `decision_engine/`: decides whether an agent should pursue a task. Tested offline (114 tests).
- `interfaces/runtime_services.py`: an `ExecutionDispatcher` protocol that would send an awarded
  task to us. **No implementation exists**, and their demo's dispatcher raises "not part of this
  demo".
- Bidding (their WP3) and dispatch (WP4) are not built.

Our side is ready for them. The formats are in [API.md](API.md).

## 0G Storage

Model binaries can be stored on 0G Storage instead of IPFS (`MODEL_STORAGE_BACKEND=0g`). The
adapter runs the official `0g-storage-client` CLI, pinned in `docker/base.Dockerfile`. It uses the
Galileo RPC and the indexer `https://indexer-storage-testnet-turbo.0g.ai`. One round trip has run on
Galileo testnet. Mainnet has not been used.

The upload needs a funded signing key. The CLI takes that key as an argument, so it is visible to
other processes on the same host during the upload.

## What we need

Each ask says what it blocks. "Blocks" means we cannot finish that part of the flow without it.

### From Team A

1. **Resolved, DRIFT-006: prover allow-list** (was blocking proof submission). `PROVER` is null in
   `deployments/galileo.json`. Someone with TaskManager ownership must run
   `setProver(<our proof-submitter address>, true)`. We will send the address. It is derived from
   our signer key.
2. **Who calls settlement (blocks payment).** `SettlementContract` exposes `settleTask(bytes32)` and
   `settle(bytes32, address)`. Our submitter only calls `submitProofs`. We need to know whether
   settlement is triggered by the contracts after `submitProofs`, or by us, and with which
   arguments.
3. **Resolved, DRIFT-011: bid and accept flow** (was blocking job auth on a real task). Our job API accepts a request only
   if the signing wallet is `taskWorker(taskId)`. So the agent's wallet has to be the accepted
   worker. We need: who may call `placeBid` and `acceptBid`, the order of calls for one task, and
   what the `capabilityProofHash` argument of `placeBid` should contain.
4. **Resolved, DRIFT-007: Galileo RPC** (was blocking running on the real chain). We use the
   public 0G RPC `evmrpc-testnet.0g.ai` today. We need the endpoint Team A runs or recommends, and
   any rate limit.
5. **Deployment start blocks.** The listener scans from a block number. We need the block where
   TaskManager and Escrow were deployed, so we do not scan from genesis.
6. **Finality policy (affects when we act on an event).** The number of confirmations before an
   event is final, the block time, and what happens on a reorg. We currently act at the head.
7. **Resolved, DRIFT-013: `ProofRejected` reason codes** (were blocking correct dead-letter reasons). The meaning of each
   `reason` value, and whether a rejected proof can be resubmitted.
8. **`MAX_PROOF_BATCH` value and partial-batch semantics.** The number, and confirmation that a
   partially accepted batch is final for the accepted entries.
9. **Resolved, DRIFT-014: `descriptionHash` and the model mapping** (was blocking real requests). What the
   hash is computed over, so we can map a task to a registry model. We have proposed that the
   registry key is Team C's model id, and `descriptionHash` is `keccak(model_id)`. We need Team A
   to confirm the hash rule.
10. **Model CID on chain.** `ProofSubmitted` carries `modelCid`, and `recordArtifact(bytes32, string)`
    also exists. We need to know which one is the record of the model CID.
11. **Resolved, DRIFT-009: funding and payment token.** Funding is ERC-20. We need the token to use, its decimals (18 per
    the handoff), and how the 81/9/10 split is applied: which contract does it, and does it pay
    `taskWorker`.
12. **Deadlines.** What the contracts do after `deadline`, and whether we should skip expired tasks
    (the spec says to).
13. **Gas and funds for testnet.** The proof-submitter signer and Team C's agent wallets need test
    0G. Confirm the faucet or the funding route.
14. **Change channel.** Who is the named contact for ABI or address changes, and how changes are
    announced.

### From Team C

1. **Which endpoint, and which flow (blocks the dispatcher).** Their `ExecutionDispatcher` should
   call `POST /challenges`, then `POST /jobs` (async, recommended, since a dispatch is not
   instant), then poll `GET /jobs/{id}`. `POST /inference` is the synchronous alternative, for
   runs under 30 s. Confirm which one each use case uses.
2. **Wallet signing (blocks auth).** The request must be signed by the agent's wallet, using EIP-191
   `personal_sign`. We need to know the wallet type (their AgentKit wallet or another), that it can
   sign arbitrary messages, and where the key is held. The signing address must equal the on-chain
   worker, so it is the wallet that bids.
3. **Bidding and dispatch timeline (blocks the end-to-end flow).** An owner and a date for their
   WP3 (bidding) and WP4 (dispatch). Today there is no code that places a bid or calls our API.
4. **Trigger (open decision).** Team C proposes `BidAccepted`. The spec says `TaskCreated` and
   `TaskFunded`. Their dispatcher is described as running after an award, which matches
   `BidAccepted`. The company and Team C need to decide. Both modes are built.
5. **Model mapping (DRIFT-014).** Which model ids their agents request, and agreement on the
   registry key. A request for an unknown model returns 404.
6. **Input and output format (DRIFT-015).** Input is a base64 `.npy` tensor, with the shape and
   dtype (float32) agreed. Output comes back as a base64 `.npy` tensor in the receipt. Confirm, or
   propose JSON with its own encoder.
7. **Correlating responses to tasks (DRIFT-012).** Our receipt does not carry `task_id`. `GET
   /jobs/{id}` does, and the job id is returned at submission. Confirm that is enough. If not, we can
   add `task_id` to the receipt.
8. **Workload scope.** Our TEE runs ONNX models up to 500 MB, and zkML proofs cover the
   architectures `linear`, `cnn`, `mlp` and `transformer_small`. Their decision engine calls LLM
   APIs (Gemini or OpenAI). Confirm that no LLM call goes through us, or say which workload must be
   proven.
9. **Retry and failure handling.** Our states are `queued`, `running`, `completed` and `failed`, with
   error codes listed in [API.md](API.md). Settlement is `pending` until the proof is submitted. We
   send `Retry-After: 5` while a job runs. Confirm their retry policy (we assumed 2–60 s backoff).
10. **Frontend read path.** Their Next.js frontend needs job status and results. Our only read is
    `GET /jobs/{id}`, which needs a fresh signed challenge from the worker. Confirm whether their
    backend does the reads, or whether results should come from Team A's events.
11. **Sample traffic (blocks conformance).** Three to five real request payloads with their expected
    responses. `scripts/ops/team_c_conformance.py` checks them.
12. **Staging.** A staging URL, its availability, and the person who triggers the cross-team run.

## Shared settings

| Setting | Value |
| --- | --- |
| Chain | 0G Galileo testnet, chain id 16602 |
| TaskManager | `0xA55f34e4137aD55f48aB268d4361F2225C4B5078` |
| EscrowContract | `0x5B685Ca03A8072FB3f9721Dd7017Ed0D8C719217` |
| SettlementContract | `0x45D1a7AF404D2EDf5E5c23C7c0b6338227b9BC4E` |

The addresses come from Team A's handoff (`team_a_assets/team_a.txt`) and
`deployments/galileo.json`. Check them when Team A changes them.

## Status update, 2026-11-02

Team A answered the asks. Recorded here, with the step-one changes:

- **Prover allow-list:** confirmed. Our proof-submitter address,
  `0x28FB61Dc27a37091f53C0c37b5026AdBbF5E1F46`, was sent. Team A will run `setProver`. Until then
  `PROVER` stays null in `deployments/galileo.json`.
- **Start block:** `EVENT_START_BLOCK=61149114`. A fresh listener reads from there, not genesis.
- **Confirmations:** `CONFIRMATIONS=2`. Detection happens at the head. Dispatch waits for two blocks.
- **Addresses:** Settlement, Staking, Identity, Treasury and the TEST token are in
  `deployments/galileo.json`, `.env.example` and the config. Blank values are filled from the record.
- **Settlement:** `settleTask` is permissionless, and it is not called automatically after
  `submitProofs`. Our call is still to be decided (step 4).
- **Bids:** create, place bid, fund, then `acceptBid` by the creator. Once accepted,
  `taskWorker` returns the bidder, and our auth check passes.
- **Descriptions:** `descriptionHash = keccak256(bytes(model_id))`, agreed with Team A. The registry
  indexes each model under this hash, and tasks resolve through that index.
- **Rejections:** `ProofRejected` carries a `uint8` code: 1 TaskNotFound, 2 InvalidStatus,
  3 ProofAlreadySubmitted, 4 InvalidPayload. Codes 1–3 are final. Code 4 and transient errors can be
  resubmitted (step 3).
- **Limits:** `MAX_PROOF_BATCH` is 50, and a bad entry never reverts the batch.
- **Deadlines:** the contracts accept proofs and settlement after the deadline. Our listener still
  skips expired tasks when it starts them.

### Settlement and deadlines, 2026-11-02

- **Settlement:** `SETTLE_AFTER_PROOF=true` makes the proof submitter call `settleTask` for each
  accepted proof. The `TaskSettled` payout is stored in Redis under `proof_submitter:settlement:<task_id>`.
  It is off by default. A failed settlement never dead-letters the proof, which is already on chain.
  Job and task status endpoints do not show the payout yet.
- **Deadlines:** the listener skips a task whose deadline has passed, at creation and again before
  dispatch. After dispatch nothing checks the deadline, so a running task finishes.
- **Galileo runbook:** `scripts/ops/galileo_full_task.py` runs the create, bid, deposit, and accept
  sequence. Without `--execute` it runs a read-only preflight and sends nothing. It needs two funded
  keys (`RUNBOOK_CREATOR_PRIVATE_KEY`, `RUNBOOK_WORKER_PRIVATE_KEY`) and TEST tokens from Team A.

### Answers still to record, 2026-11-02

- **RPC (DRIFT-007):** `https://evmrpc-testnet.0g.ai`, chain id 16602.
- **Funding (DRIFT-009):** the bounty is the TEST ERC-20 (18 decimals). The creator calls `approve`,
  then `EscrowContract.deposit`.
- **Model CID:** the proof's model CID is recorded in `ProofSubmitted` (`modelCid`).
- **Settlement:** `settleTask` is permissionless and pays 81 / 9 / 10 (worker, stakers, treasury).
  Proofs and settlement are accepted after the deadline. The listener still skips expired tasks.
- **Change policy:** Team A tags releases, and changes to `deployments/galileo.json` come with a note.

## Open asks, 2026-11-02

| Ask | Who | Status |
| --- | --- | --- |
| TEST tokens for the creator wallet (the escrow deposit needs them) | Team A | To ask |
| A funded worker wallet to bid (Team C's agent wallet, or a stand-in) | Team C | Open |
| A versioned release tag, so our pinned ABIs follow their change policy | Team A | To send |
| The `capabilityProofHash` value for bids | Team C | Open. We use `bytes32(0)` until then. |

Draft for the release tag:

> Could you tag the TaskManager, EscrowContract and SettlementContract ABIs at the version we are
> pinned to (`team_a_assets/`), and tell us the tag for each future change? Our build reads these
> ABIs directly, so we want to pin a tagged version and follow your change policy.

## Keys and funding, 2026-11-02

- **Keys are separate.** Proof submission uses the signer Team A allow-listed,
  `0x28FB61Dc27a37091f53C0c37b5026AdBbF5E1F46`. 0G uploads use their own key,
  `0xCCF89EDCd8c5d2F1C4Ff7B5194a02a20618F311D`, which is set in `.env` only.
- **0G test tokens:** the faucet is at faucet.0g.ai and limits each wallet to 0.1 0G a day. The
  upload address is entered, but the faucet requires a CAPTCHA, which needs a person to complete.
- **TEST tokens:** not yet received. We will ask Team A.

