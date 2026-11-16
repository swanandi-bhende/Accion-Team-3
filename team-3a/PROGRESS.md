# Team 3a — Progress Report

**Subsystem:** Blockchain & Smart Contracts
**Verified this session:** `npx hardhat test` → **47 passing** · `pytest tests/` → **20 passing**
**Correctness properties: 22 of 22 covered**

---

## Provenance

Everything here was built from a clean extraction of `team3a-week1-progress.zip`.
The container also held an unattributed near-complete copy of this project; **none of
it was read, copied, or referenced.** Your week-1 files (`EscrowContract.sol`,
`IEscrow.sol`, `ITaskManager.sol`, `TestERC20.sol`, `EscrowContract.test.ts`,
`hardhat.config.ts`, `.env.example`) are unchanged except for one line noted below.

Still worth resolving: that other copy contained an `ArtifactRegistry.sol` mapping to
no requirement in your spec. If it's a teammate's work, someone added scope.

---

## Checklist

`[x]` done and tested · `[~]` implemented against a reference backend, not a live service · `*` optional per plan

### Waves 0–4 — Smart contracts
- [x] **1.1** Hardhat project init — *week 1, yours*
- [x] **1.2** Python backend structure — *week 1, yours; packages now populated*
- [x] **1.3** Shared interfaces — *yours + `IIdentity`, `ISettlement`, `ITaskStatus`*
- [x] **2.1 / 2.2\* / 2.3\*** EscrowContract + tests — *week 1, yours*
- [x] **3.1** IdentityContract
- [x] **3.2\*** Properties 4–6 · **3.3\*** unit tests
- [x] **4.1** TaskManager
- [x] **4.2** SettlementContract
- [x] **4.3\*** Properties 7–10 · **4.4\*** unit tests
- [x] **5.1** StakingContract
- [x] **5.2\*** Properties 11–13 · **5.3\*** unit tests
- [x] **6** Checkpoint — smart contracts complete

### Waves 5–7 — Services
- [~] **7.1** Storage backend — `IPFSBackend` written; tests run against `InMemoryBackend`
- [x] **7.2** On-chain hash recording flow
- [x] **7.3\*** Property 14 · **7.4\*** storage unit tests
- [x] **8.1** FastAPI `/read` + `/submit` + `/health`
- [x] **8.2** Pydantic validation and normalised error envelope
- [x] **8.3\*** Properties 15–16 · **8.4\*** RPC unit tests
- [x] **9** Checkpoint — on-chain and service layers complete

### Waves 8–9 — Legal intelligence
- [~] **10.1** LangGraph workflow — four nodes, real `StateGraph`; clause identification is rule-based, not LLM
- [x] **10.2\*** Property 17 · **10.3\*** agent unit tests
- [~] **11.1** NebulaGraph schema + client — schema DDL written; tests run against `InMemoryGraph`
- [x] **11.2\*** Properties 18–19 · **11.3\*** graph unit tests
- [~] **12.1** Weaviate schema + client — schema written; tests run against `InMemorySearchEngine`
- [x] **12.2\*** Properties 20–22 · **12.3\*** search unit tests

### Waves 10–11 — Integration
- [x] **13.1** Agent → graph → search index wiring
- [x] **13.2** Settlement → Staking yield wiring
- [x] **13.3** Deployment script — exercised, writes `deployments/<network>.json`
- [x] **13.4\*** Integration tests — full lifecycle + event filtering
- [x] **14.1** docker-compose complete — 3 Dockerfiles added, volumes, healthcheck
- [ ] **15** Final checkpoint — needs a live `docker compose up` run

---

## What "80%" honestly means here

Every task except the final checkpoint has code and passing tests. But four components
are implemented against **reference backends rather than live services**, and you should
not describe them as production-integrated:

| Component | Real implementation | What the tests actually exercise |
|---|---|---|
| IPFS storage | `IPFSBackend` (Kubo HTTP API) | `InMemoryBackend` |
| NebulaGraph | `NebulaGraphClient` + schema DDL | `InMemoryGraph` |
| Weaviate | `WeaviateSearchEngine` + class schema | `InMemorySearchEngine` (token-overlap cosine) |
| Clause identification | LangGraph `StateGraph`, 4 nodes | Rule-based keyword extractor, no LLM |

This is deliberate and structured so the swap is cheap: in each case the live class
**inherits from the reference one and overrides only transport**, so graph semantics,
ranking, filtering and threshold behaviour cannot drift between what's tested and
what deploys. `RuleBasedExtractor` exposes `extract(text) -> list[Clause]`; an
LLM-backed extractor implementing that one method drops in with no other changes.

What this buys you: the suite runs offline, in CI, with no API keys, in about a
second. What it costs you: **nobody has yet proven this talks to a real Nebula
cluster or a real Weaviate instance.** That's the remaining work, and it's exactly
what Checkpoint 15 is for.

Binary PDF/DOCX decoding is also not wired up — `extract_text` raises
`NotImplementedError` for those and expects pre-extracted text. The formats are
accepted and validated per Req 9.4; only the decoder is missing.

---

## Design decisions worth defending in review

**Worker share as remainder (Req 3.5, Property 7).** Staker and treasury use floor
division; the worker gets `total - staker - treasury`. Computing all three as
percentages would strand up to 2 wei in escrow on every settlement. Property 7 tests
this across ten magnitudes from 1 wei up.

**Empty staking pool reroutes to treasury.** `distributeYield` reverts on an empty
pool, which would make the whole `settle()` call revert and permanently strand a task.
Settlement checks `totalStaked()` first and folds the 9% into the treasury share
instead. The three parts still sum to the total, so Property 7 holds either way.
This is a judgment call — if your spec intends stakers to always exist, say so and
it becomes a hard revert instead.

**Registration state separate from metadata hash (Req 2.3, Property 5).**
`bytes32(0)` is a legal hash. Inferring registration from `_metadata[w] != 0` lets
someone who registered a zero hash register twice. Pinned by a dedicated edge test.

**Reward-per-token accumulator for staking.** O(1) per staker regardless of pool
size, and a staker who joins after a distribution is structurally unable to claim
any of it — tested directly.

**`markSettled` before fund release.** TaskManager rejects a second settlement, so
the status write doubles as the reentrancy guard on the three `release()` calls.

---

## Two open items for the team

**1. One line changed in your week-1 file.** `IStaking.sol` gained
`totalStaked() external view returns (uint256)` — Settlement needs it to detect an
empty pool. Purely additive. Everything else of yours is untouched. I also created
`ITaskStatus.sol` as a separate interface rather than editing your `ITaskManager.sol`;
merging the two is a reasonable cleanup if you'd rather have one surface.

**2. Escrow still accepts deposits against non-existent tasks.** `EscrowContract`
never consults `TaskManager`, and `createTask`'s `bountyAmount` is recorded but never
reconciled with what's escrowed. Funds deposited against a bogus `taskId` are
permanently unreachable, since `settle()` requires a `Completed` task record. There's
a test in `Integration.test.ts` that documents this rather than asserting it's
correct. Your spec doesn't require the check — but it's the most serious hole in the
system and I'd rather flag it than quietly patch a requirement you didn't write.

---

## Running it

```bash
npm install && npx hardhat test          # 47 passing
pip install -r requirements.txt
python -m pytest tests/ -q               # 20 passing

npx hardhat node                         # terminal 1
npx hardhat run deploy/deploy.ts --network localhost
uvicorn backend.rpc_wrapper.main:app --reload
```


---

## Completion update

**Verified:** `npx hardhat test` → **67 passing** · `pytest tests/` → **62 passing** ·
`deploy` + `smoke:local` run against a live local Hardhat node (create → bid → fund → accept →
`submitProofs` → `settleTask`, worker paid exactly 81%) · RPC wrapper exercised against that
node (`/config`, `/tasks/{id}`, `/upload` → sign → `/submit` → hash readable on-chain).

### Added
- `TaskManager.submitProofs` + prover role (`setProver`), batch cap 50, per-item `ProofRejected`
- On-chain bids (`placeBid`, `acceptBid`), `taskWorker`, and permissionless `settleTask`
- `recordArtifact` / `artifactHash` — Requirement 6.2 had no contract function before
- RPC wrapper: `GET /config`, `GET /tasks/{id}`; `/upload` returns the unsigned recording tx;
  ABI fallback to `exports/` so containers don't need a compile step
- Deploy manifest records start block, compiler, commit, token symbol/decimals, prover;
  `verify-contracts.ts`, `lifecycle-smoke.ts`, Etherscan config
- `HANDOFF.md` for Teams 3b and 3c

### Bugs found and fixed
- `LLMClauseExtractor` sent `ANTHROPIC_API_KEY` to Groq; used a model name that does not exist;
  trusted model-supplied offsets and confidence (Property 17). Rewritten: key → own provider
  only, offsets re-derived from the text, confidence clamped.
- `NebulaGraphClient` built nGQL by string interpolation of contract text / IDs (injection);
  never ran `USE <space>`. Both fixed.
- `WeaviateSearchEngine.connect` passed a full URL as host, hard-coded port 8080 and omitted
  the gRPC arguments; compose ran Weaviate with no vectorizer so `near_text` could not work.
  Fixed, and a `text2vec-transformers` service was added.
- `/upload` recorded nothing on-chain (a `pass` stub). Fixed.
- `/submit` returned the tx hash without `0x` under web3 v8. Fixed.
- `.env.example` defaulted `DEPLOYMENT_NETWORK=hardhat` while `deploy:local` writes
  `localhost.json`; the README's "defaults work as-is" was false. Fixed.
- `backend/Dockerfile` copied gitignored `artifacts/` and `deployments/`, so a clean clone
  could not build. Fixed.
- `agent-service` always used in-memory stores even under compose. Now `USE_LIVE_BACKENDS`.

### Second audit against the requirements document — gaps found and closed
- **Req 6.5:** `ArtifactStore.load` compared an IPFS CID to a plain sha256, so every real
  retrieval would have failed the integrity check. Now verified via Kubo's own addressing.
- **Req 7.3:** nothing bounded read latency, and web3 v7+ silently retries timeouts (a 2 s
  budget would have become ~12 s). Reads now use a 2 s connection with retries disabled.
- **Req 10.2:** `depends_on`, `conflicts_with` and `supersedes` were defined but never created.
  Rule-based cue detection added (conservative: no cue, no edge).
- **Req 10.4 / task 11.3:** no queryable Cypher interface and no test. Added a single-hop
  `MATCH … RETURN id()` subset (same text runs on Nebula), `find_paths`, and service endpoints.
- **Req 10.3 / 11.2:** query timeouts of 3 s (Nebula) and 2 s (Weaviate) now configured.
- **Task 14.1:** only 1 of 8 services had a health check. All do now; Nebula's storage host
  is registered (`ADD HOSTS`) because a bare compose cluster cannot create a space otherwise.
- **Task 7.4 timeout test** and the **Req 6.4** path were untested; now covered.

### Still open — cannot be done without your environment
- Sepolia deploy and Etherscan verification (deployer key, RPC URL, token address, API key)
- Docker run of Nebula / Weaviate / IPFS (Checkpoint 15)
- LLM extractor against a real model (API key)
