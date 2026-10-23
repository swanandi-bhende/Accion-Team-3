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

1. **Prover allow-list (blocks proof submission on Galileo).** `PROVER` is null in
   `deployments/galileo.json`. Someone with TaskManager ownership must run
   `setProver(<our proof-submitter address>, true)`. We will send the address. It is derived from
   our signer key.
2. **Who calls settlement (blocks payment).** `SettlementContract` exposes `settleTask(bytes32)` and
   `settle(bytes32, address)`. Our submitter only calls `submitProofs`. We need to know whether
   settlement is triggered by the contracts after `submitProofs`, or by us, and with which
   arguments.
3. **Bid and accept flow (blocks the job auth on a real task).** Our job API accepts a request only
   if the signing wallet is `taskWorker(taskId)`. So the agent's wallet has to be the accepted
   worker. We need: who may call `placeBid` and `acceptBid`, the order of calls for one task, and
   what the `capabilityProofHash` argument of `placeBid` should contain.
4. **Official Galileo RPC, HTTP and WebSocket (blocks running outside a shared host).** We use the
   public 0G RPC `evmrpc-testnet.0g.ai` today. We need the endpoint Team A runs or recommends, and
   any rate limit.
5. **Deployment start blocks.** The listener scans from a block number. We need the block where
   TaskManager and Escrow were deployed, so we do not scan from genesis.
6. **Finality policy (affects when we act on an event).** The number of confirmations before an
   event is final, the block time, and what happens on a reorg. We currently act at the head.
7. **`ProofRejected` reason codes (blocks correct dead-letter reasons).** The meaning of each
   `reason` value, and whether a rejected proof can be resubmitted.
8. **`MAX_PROOF_BATCH` value and partial-batch semantics.** The number, and confirmation that a
   partially accepted batch is final for the accepted entries.
9. **`descriptionHash` meaning and the model mapping (DRIFT-014, blocks real requests).** What the
   hash is computed over, so we can map a task to a registry model. We have proposed that the
   registry key is Team C's model id, and `descriptionHash` is `keccak(model_id)`. We need Team A
   to confirm the hash rule.
10. **Model CID on chain.** `ProofSubmitted` carries `modelCid`, and `recordArtifact(bytes32, string)`
    also exists. We need to know which one is the record of the model CID.
11. **Funding and payment token.** Funding is ERC-20. We need the token to use, its decimals (18 per
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
