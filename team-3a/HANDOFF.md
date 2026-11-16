# Team 3a → Teams 3b and 3c: integration handoff

Everything the other two teams asked for, in one place. Machine-readable versions live in
`exports/` (ABIs, event signatures) and `deployments/<network>.json` (addresses, chain ID,
start block, compiler, token decimals). **If this file and the deployment JSON disagree,
the JSON wins** — it is written by the deploy script.

> **Status of the Galileo deployment:** Deployed and verified on 0G Galileo testnet (chain ID 16602).
> All contract sources are verified on Chainscan: https://chainscan-galileo.0g.ai

## 1. Contracts and addresses

| Contract | Galileo address | Purpose |
|---|---|---|
| `TaskManager` | `0xA55f34e4137aD55f48aB268d4361F2225C4B5078` | Tasks, bids, proofs, artifact hashes, task status |
| `EscrowContract` | `0x5B685Ca03A8072FB3f9721Dd7017Ed0D8C719217` | Holds bounties (`deposit`) |
| `SettlementContract` | `0x45D1a7AF404D2EDf5E5c23C7c0b6338227b9BC4E` | 81 / 9 / 10 split (`settleTask`) |
| `StakingContract` | `0x41eCc7485d2Bbc188B4f638871ca3f00D2c65C38` | Staker yield |
| `IdentityContract` | `0xC70c8865eACC340AB15Af8d4e726F438Cd639Db9` | Participant registration |
| ERC-20 token (`TEST`) | `0x22374BC6FF12FF2Bf407Da332F16Ee65D52CE0EC` | Backs escrow and staking (18 decimals) |
| `Treasury` | `0x21dbf28f735063d8D43D7DB6Aa1550b9724755f2` | Platform treasury |

Settlement is a **separate** contract from TaskManager and Escrow.

## 2. Source reference
- Solidity **0.8.20**, optimizer on, **200 runs**, OpenZeppelin 5.x.
- `deployments/<network>.json` records `gitCommit`, compiler settings and (after
  `npm run verify:sepolia`) an `explorer` map of verified-source links.
- Tag the commit that was deployed (`v0.1.0`) and quote the tag when announcing addresses.

## 3. ABIs
`exports/<Contract>.abi.json` (bare ABI JSON) and `exports/abis.json` (all in one object).
`exports/index.json` lists every event signature and the deployed addresses. CI fails if the
contracts change without `exports/` being regenerated. The RPC wrapper's `GET /config` returns
the same addresses at runtime.

## 4. Events (final)

```
TaskCreated(bytes32 indexed taskId, address indexed creator, bytes32 descriptionHash, uint256 deadline, uint256 bountyAmount)   // TaskManager
TaskFunded(bytes32 indexed taskId, address indexed funder, uint256 amount)                                                       // EscrowContract
TaskSettled(bytes32 indexed taskId, address indexed worker, uint256 workerAmount, uint256 stakerAmount, uint256 treasuryAmount)  // SettlementContract
ProofSubmitted(bytes32 indexed taskId, bytes32 proofHash, string modelCid, bytes32 resultHash, address indexed submitter)       // TaskManager
ProofRejected(bytes32 indexed taskId, uint8 reason)                                                                              // TaskManager
BidPlaced(bytes32 indexed taskId, address indexed bidder, uint256 price, uint64 etaSeconds, bytes32 capabilityProofHash)        // TaskManager
BidAccepted(bytes32 indexed taskId, address indexed worker)                                                                      // TaskManager
ArtifactRecorded(bytes32 indexed taskId, string contentHash, address indexed recorder)                                           // TaskManager
```

**`taskId` is `bytes32`** everywhere. Represent it as a `0x` + 64 hex string off-chain
(`task_id: str` in 3b/3c code is fine, but it must be that hex string, not a UUID).
`TaskFunded.amount` is **per deposit**, not cumulative.

## 5. `submitProofs` (3b → 3a)

```solidity
function submitProofs(
    bytes32[] calldata taskIds,
    bytes32[] calldata proofHashes,
    string[]  calldata modelCids,
    bytes32[] calldata resultHashes
) external returns (uint256 accepted);   // TaskManager
```

| Aspect | Behaviour |
|---|---|
| Caller | Must hold the prover role, else reverts `NotProver()` |
| Arrays | All four the same length, else reverts `ArrayLengthMismatch()` |
| Batch limit | `MAX_PROOF_BATCH` = **50**; more reverts `BatchTooLarge()`. Empty batch is a no-op |
| Per-item failure | **Never reverts the batch.** The item is skipped and `ProofRejected(taskId, reason)` is emitted |
| Reasons | `1` unknown task · `2` task not Open/InProgress (e.g. already Completed/Settled) · `3` proof already submitted · `4` zero `proofHash`/`resultHash` or empty `modelCid` |
| Success | Stores the proof, emits `ProofSubmitted`, moves the task to **Completed** (settleable) |
| Return value | Number of proofs accepted. Read per-item outcomes from the events |
| Field mapping | `proofHash` = `0x` + sha256 hex from 3b's payload; `resultHash` = `0x` + `output_hash`; `modelCid` = the CID string |
| Reading back | `getProof(taskId)` → `(proofHash, modelCid, resultHash, submitter, submittedAt)` |

Because a bad item cannot poison a batch, 3b's retry loop only needs to retry on transaction
failure (nonce, gas, network), not on per-proof problems — watch `ProofRejected` instead.
Deadlines are **not** enforced on proofs.

### Authorisation
The proof-submitter address must be granted the prover role by the 3a owner:
`TaskManager.setProver(address, true)` (or set `PROVER_ADDRESS` before running the deploy
script). It can be revoked with `setProver(address, false)`. 3b: send your signer **address**
to 3a; never send the key through the repo.

## 6. Task lifecycle (answers to 3b item 9)

```
createTask ─► (agents) placeBid ─► creator deposits (approve + deposit) ─► creator acceptBid
   Open                Open                         Open                    InProgress
                                                                                │
        settleTask (anyone) ◄── Completed ◄── submitProofs (3b prover) ◄────────┘
            Settled
```

- **Funding cannot precede creation.** `deposit` reverts for an unknown task.
- **Bids:** `placeBid(taskId, price, etaSeconds, capabilityProofHash)` while Open, one per
  bidder. The creator calls `acceptBid(taskId, bidder)`: the bidder becomes the worker and the
  task moves to InProgress. `taskWorker(taskId)` returns the worker.
- **Proof → Completed:** `submitProofs` completes Open *or* InProgress tasks, so 3b does not
  need to know about bids. (Creator `markComplete` still exists as a manual fallback.)
- **Settlement:** `SettlementContract.settleTask(taskId)` is **permissionless**. Anyone —
  3c's agent, the worker, a relayer — may call it once the task is Completed, funded to at
  least `bountyAmount`, and has an accepted worker. It pays the worker, the stakers and the
  treasury 81 / 9 / 10 (worker gets the rounding remainder). The owner-only
  `settle(taskId, worker)` remains as a fallback. A second settlement reverts.
- **Dispatch rule for 3b:** treat a task as fundable-complete when the **sum** of its
  `TaskFunded.amount` values is ≥ `bountyAmount`, not on the first `TaskFunded`.
- **Deadline:** must be in the future at `createTask`; not enforced afterwards.
- **Bounty unit:** ERC-20 smallest units — **not native ETH**. Read `TokenDecimals` from the
  manifest; do not assume 18. Depositors must `approve(EscrowContract, amount)` first.
- **Task ID:** `keccak256(abi.encode(creator, taskCount, descriptionHash))`, returned by
  `createTask` and carried in `TaskCreated`. Read it from the event; do not predict it.

## 7. Network config (3b item 7)
- **0G Galileo Testnet**, **chain ID 16602**. `deployments/galileo.json` → `chainId`, `deployedAtBlock`.
- Explorer: `https://chainscan-galileo.0g.ai` — use `deployedAtBlock` as the event scan start.
- RPC: `https://evmrpc-testnet.0g.ai` (default public endpoint; use a dedicated node for
  production workloads — official docs suggest QuickNode / Ankr / dRPC for heavy usage).
- Gas token: **0G** (native). No ERC-20 wrapping needed for gas.
- Gas: a full 50-item `submitProofs` batch measured **≈8.4M gas** (~170k per proof, mostly
  storing the CID string). Always `estimateGas` and add ~20% headroom.
- Local development: `npm run node` (chain ID 31337, `http://127.0.0.1:8545`).

## 8. Funded signer (3b item 8)
3b generates a fresh key for the proof submitter and sends 3a its **address**. 3a funds it with
0G gas token from the faucet at `faucet.0g.ai` and grants the prover role by setting
`PROVER_ADDRESS` before `npm run deploy:galileo`, or later via `TaskManager.setProver(addr, true)`.
The private key stays with 3b — never in the repo, issues, or group chat.

## 9. Finality (3b item 10)
Galileo block time: check `deployments/galileo.json` after deploy for live timing.
Recommendation: **observe** events at the head (so the 15 s detection target is met) and
**dispatch inference after 2 confirmations**. Because events can be re-orged, key all
processing by `(transactionHash, logIndex)` and re-check `getTask` before dispatch.
3a does not enforce this; it is a consumer-side policy.

## 10. Change policy (3b item 11)
- **Owner:** the 3a lead (name in the team channel). **Channel:** the shared integration
  channel; breaking changes are announced there *before* merge.
- Breaking = any change to an event signature, function signature, revert error, or
  `taskId` encoding. Additive changes (new events/functions) are non-breaking.
- Every deploy is tagged (`vMAJOR.MINOR.PATCH`); a breaking change bumps MAJOR and ships with
  new `exports/` and a new `deployments/` manifest. Contracts are not upgradeable — a new
  version means new addresses.

## 11. For Team 3c

| Need | Answer |
|---|---|
| Addresses / ABIs / token decimals | `exports/`, `deployments/galileo.json` (`TokenDecimals`), or `GET /config` on the RPC wrapper |
| Fund a bounty | `token.approve(escrow, amount)` then `EscrowContract.deposit(taskId, amount)`; amounts use token decimals (the frontend's `parseEther` is only right for 18-decimal tokens — use `parseUnits(value, TokenDecimals)`) |
| Create a task | `TaskManager.createTask(descriptionHash, deadline, bountyAmount)`. The creator wallet is the only one that can `acceptBid` / `markComplete` |
| Bid | `TaskManager.placeBid(taskId, price, etaSeconds, capabilityProofHash)` |
| Trigger settlement | After 3b's proof lands: `SettlementContract.settleTask(taskId)` — no special role needed |
| Read results | `GET /tasks/{taskId}` on the RPC wrapper returns status, worker, escrow balance, proof (`model_cid`, hashes) and artifact hash in one call; or subscribe to `ProofSubmitted` / `TaskSettled` |
| Upload a model artifact | `POST /upload` (multipart: `task_id`, `file`) returns `content_hash` and an **unsigned** `record_tx {to,data}`. Sign it with your wallet and `POST /submit` it |
| Submit a signed tx | `POST /submit {"signed_tx": "0x…"}` → `{"tx_hash": "0x…"}` |

The RPC wrapper never holds keys. Items in Team C's list that concern 3b ↔ 3c only
(`InferenceRequest` payload, auth for `POST /inference`, model registry, staging URL) are
between those two teams — 3a is not on that path.

## 12. Verifying the whole flow
```bash
npm run node                 # terminal 1
npm run deploy:local         # terminal 2 (set PROVER_ADDRESS to 3b's address first)
npm run smoke:local          # create → bid → fund → accept → submitProofs → settleTask
```

For Galileo (needs a funded second key in `SMOKE_WORKER_KEY` and a funded deployer):
```bash
npm run deploy:galileo       # deploy all five contracts
npm run smoke:galileo        # not yet defined — extend lifecycle-smoke.ts for Galileo if needed
```

Explorer links after deploy+verify: `deployments/galileo.json` → `.explorer`

