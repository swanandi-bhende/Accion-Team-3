# Team 3a — Blockchain & Smart Contracts subsystem

On-chain escrow, identity, task management, settlement and staking for the
Autonomous Web3 Research & On-Chain Settlement Agents platform, plus the off-chain
RPC wrapper, storage integration, and legal-intelligence layer.

**Status:** 67 Solidity tests + 62 Python tests passing. All 22 correctness
properties from the design doc are covered. Integration with Teams 3b and 3c is
documented in [`HANDOFF.md`](HANDOFF.md); requirement-by-requirement status, including every
deviation from the spec, is in [`SPEC_COMPLIANCE.md`](SPEC_COMPLIANCE.md).

---

## Quick start

```bash
# Contracts
npm install
npm run compile
npm test                 # 47 passing

# Backend
pip install -r requirements.txt
npm run test:py          # 20 passing

# Both
npm run test:all
```

Local end-to-end check (after `npm run node` and `npm run deploy:local`):

```bash
npm run smoke:local      # create -> bid -> fund -> accept -> submitProofs -> settleTask
```

Local deployment:

```bash
cp .env.example .env     # local defaults work as-is
npm run node             # terminal 1
npm run deploy:local     # terminal 2 — writes deployments/localhost.json
uvicorn backend.rpc_wrapper.main:app --reload
```

The deploy script writes contract addresses to `deployments/<network>.json`.
The backend reads that file — **addresses are never configured by hand.**

---

## For Team B — consuming our events

Run `npm run export:abi`. It writes `exports/`:

| File | Contents |
|---|---|
| `exports/abis.json` | Every contract ABI in one object |
| `exports/<Contract>.abi.json` | One ABI per contract |
| `exports/index.json` | Event signatures + deployed addresses per network |

The three events the platform is built around:

```
TaskCreated(bytes32 indexed taskId, address indexed creator,
            bytes32 descriptionHash, uint256 deadline, uint256 bountyAmount)
TaskFunded(bytes32 indexed taskId, address indexed funder, uint256 amount)
TaskSettled(bytes32 indexed taskId, address indexed worker,
            uint256 workerAmount, uint256 stakerAmount, uint256 treasuryAmount)
```

`taskId` is indexed on all three, so a single task's full history can be filtered.
CI fails if a contract changes without `exports/` being regenerated, so what's
committed is always current. The full event list, the `submitProofs` spec and the
task lifecycle are in [`HANDOFF.md`](HANDOFF.md).

---

## Contracts

| Contract | Requirement | Purpose |
|---|---|---|
| `EscrowContract` | 1 | Holds task bounties. Only SettlementContract can release. |
| `IdentityContract` | 2 | On-chain participant registration. |
| `TaskManager` | 4, 6.2 | Task records, bids, proof anchoring (`submitProofs`), artifact hashes. Status Open → InProgress → Completed → Settled. |
| `SettlementContract` | 3 | Distributes 81% worker / 9% stakers / 10% treasury. |
| `StakingContract` | 5 | Stake, unstake, proportional yield, claim. |

Deploy order matters: Escrow, Staking and TaskManager each need the Settlement
address, but Settlement needs theirs. The cycle is broken by deploying them
unwired and calling one-time `setSettlementContract` setters afterwards.
`deploy/deploy.ts` handles this.

### Design decisions

**Worker share is the remainder.** Staker and treasury use floor division; the
worker gets `total - staker - treasury`. Percentages on all three would strand up
to 2 wei per settlement. Tested from 1 wei upward (Property 7).

**Empty staking pool reroutes to treasury.** `distributeYield` reverts on an empty
pool, which would revert the whole settlement and strand the task. Settlement
checks `totalStaked()` first and folds the 9% into the treasury share. The three
parts still sum to the total.

**Registration is tracked separately from the metadata hash.** `bytes32(0)` is a
legal hash, so inferring registration from a non-zero hash would let someone
register twice (Property 5).

**Proofs never revert a batch for one bad item.** `submitProofs` skips an invalid item and
emits `ProofRejected(taskId, reason)`. Team 3b retries whole batches; a revert on one bad
proof would block every good one.

**Settlement is permissionless once a worker is assigned.** `settleTask(taskId)` can be called
by anyone after a proof completes the task; the payee comes from the accepted bid. The
owner-only `settle(taskId, worker)` stays as a relayer fallback.

**`markSettled` runs before fund release.** TaskManager rejects a second
settlement, so the status write is the reentrancy guard on the release calls.

---

## Backend

| Module | Requirement | Notes |
|---|---|---|
| `backend/rpc_wrapper` | 7 | FastAPI `/read`, `/submit`, `/health`. Never holds keys — relays pre-signed transactions only. |
| `backend/storage` | 6 | Upload → integrity verify → record hash on-chain. |
| `backend/legal_agent` | 9 | LangGraph workflow, 4 nodes, 7 clause types. |
| `backend/graph` | 10 | NebulaGraph schema + cross-contract auto-linking. |
| `backend/search` | 11 | Weaviate semantic search with filters and threshold. |

### What has and has not been run live

Four components have a live implementation **and** an in-memory reference implementation.
The unit tests exercise the reference version; each live class inherits from it and
overrides only transport, so semantics cannot drift.

| Component | Live class | Unit tests run against | Run against a real service? |
|---|---|---|---|
| IPFS storage | `IPFSBackend` | `InMemoryBackend` | **No** — needs `docker compose up` |
| Legal graph | `NebulaGraphClient` | `InMemoryGraph` | **No** — needs `docker compose up` |
| Semantic search | `WeaviateSearchEngine` | `InMemorySearchEngine` | **No** — needs `docker compose up` |
| Clause identification | `LLMClauseExtractor` (Anthropic or Groq) | fake LLM clients | **No** — needs an API key |

Run `scripts/verify-live.sh` (and `scripts/verify-llm.py`) on a machine with Docker to prove
the live paths. The contracts, deploy script, RPC wrapper and PDF/DOCX decoding **have** been
exercised for real (local Hardhat node + real files).

`POST /upload` returns the unsigned `recordArtifact` call rather than sending it: the wrapper
never holds keys, so the caller signs and posts it to `/submit`.

## Environment

Copy `.env.example` to `.env`. Every variable in it is read by the code; there are
no decorative entries. The one that matters most:

**`ESCROW_TOKEN_ADDRESS`** — required on any non-local chain. On the local Hardhat
node the deploy script deploys a `TestERC20` mock; on any other chain it refuses to
run without a real token address, rather than silently backing the system with a
worthless mock.

---

---

## Deploying to 0G Galileo Testnet

Galileo is the only supported public testnet target (chain ID **16602**).
There is **no mainnet deployment path**.

### Prerequisites

| Item | Where to get it |
|---|---|
| Funded deployer wallet | 0G faucet at [faucet.0g.ai](https://faucet.0g.ai) — needs 0G gas token |
| ERC-20 token address | An existing ERC-20 on Galileo; no faucet token is auto-deployed |
| RPC URL | `https://evmrpc-testnet.0g.ai` (default, already in hardhat.config.ts) |

### Step-by-step

```bash
# 1. Copy env template and fill in Galileo values
cp .env.example .env

# Required (no deploy without these two):
#   DEPLOYER_PRIVATE_KEY=0x<your_funded_deployer_key>
#   ESCROW_TOKEN_ADDRESS=0x<ERC20_address_on_galileo>

# Optional but recommended:
#   TREASURY_ADDRESS=0x<your_treasury>   # defaults to deployer if unset
#   PROVER_ADDRESS=0x<team_3b_signer>    # grant later with setProver if needed
#   OG_RPC_URL=https://...              # override if using a dedicated node

# 2. Compile (already done if you ran npm run compile)
npm run compile

# 3. Deploy all five contracts to Galileo
npm run deploy:galileo
# Writes deployments/galileo.json with addresses, tx hashes, and compiler settings.

# 4. Export ABIs (run after every deploy)
npm run export:abi

# 5. Verify source on Chainscan (optional but recommended)
npm run verify:galileo
# Uses the Etherscan-compatible API at https://chainscan-galileo.0g.ai/open/api
# No API key required (OG_EXPLORER_API_KEY defaults to placeholder "galileo").
```

### Compiler note — pragma vs. evmVersion conflict

The contracts use `pragma solidity ^0.8.20` and the compiler is kept at **0.8.20**.

The official 0G docs recommend `evmVersion: "cancun"` for Galileo. However,
`"cancun"` was added to the Solidity compiler in version **0.8.24**. Solc 0.8.20
only understands up to `"shanghai"` — setting `"cancun"` produces
`Invalid EVM version requested` at compile time.

**Resolution:** `evmVersion: "shanghai"` is set in `hardhat.config.ts`.
This is safe because:
- Galileo's EVM is Cancun-era, which is a strict superset of Shanghai.
- Shanghai bytecode runs correctly on any Cancun chain.
- None of the Cancun-only opcodes (`TLOAD`, `TSTORE`, `MCOPY`, `BLOBHASH`) are
  used in these contracts.

For Chainscan source verification, submit with **Solidity 0.8.20** and
**EVM version: shanghai**. The bytecode will match exactly.

### Environment variables added for Galileo

| Variable | Required | Description |
|---|---|---|
| `DEPLOYER_PRIVATE_KEY` | **Yes** | Funded Galileo wallet key (hex, with or without 0x) |
| `ESCROW_TOKEN_ADDRESS` | **Yes** | ERC-20 address on Galileo backing escrow and staking |
| `OG_RPC_URL` | No | Galileo RPC; defaults to `https://evmrpc-testnet.0g.ai` |
| `OG_EXPLORER_API_KEY` | No | Chainscan key; defaults to `"galileo"` (open API) |
| `TREASURY_ADDRESS` | No | 10% platform fee recipient; defaults to deployer |
| `PROVER_ADDRESS` | No | Team 3b's proof submitter; grant later with `setProver` |

### What the deploy script records in `deployments/galileo.json`

- `network`, `chainId`, `deployedAtBlock`, `deployedAt`, `deployer`, `gitCommit`
- `compiler` — version, evmVersion, optimizer settings
- `constructorArgs` — per-contract constructor inputs
- `initCalls` — every `setSettlementContract` / `setTaskManagerContract` call
- `Token`, `TokenSymbol`, `TokenDecimals`, `Prover`, `Treasury`
- Contract addresses: `EscrowContract`, `StakingContract`, `IdentityContract`,
  `TaskManager`, `SettlementContract`
- `txHashes` — deployment transaction hash per contract (from on-chain receipts)

This file is network-scoped. Running `deploy:galileo` writes `galileo.json`;
it never touches `sepolia.json` or `localhost.json`.

---

## Open items

- Galileo deploy + Chainscan verification (`npm run deploy:galileo && npm run verify:galileo`),
  then update `HANDOFF.md` with the real addresses and explorer links.
- Sepolia deploy + Etherscan verification (`npm run deploy:sepolia && npm run verify:sepolia`).
- Checkpoint 15: run `docker compose up` and `scripts/verify-live.sh` to prove the live
  Nebula / Weaviate / IPFS paths.
- Set `ANTHROPIC_API_KEY` (or `GROQ_API_KEY`) and run `scripts/verify-llm.py` to prove the LLM
  extractor against a real model.
- `EscrowContract` does not consult `TaskManager`. Deposits against a non-existent
  `taskId` are permanently unreachable, and `createTask`'s `bountyAmount` is never
  reconciled with the escrowed amount. Documented by a test in
  `test/Integration.test.ts`. Not in the spec — decide before launch.
- `ITaskStatus.sol` is separate from `ITaskManager.sol`; merging them is reasonable
  cleanup.

