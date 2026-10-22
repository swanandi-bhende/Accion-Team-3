# Legacy mock contracts — DO NOT USE

> **Archived. Do not deploy these, do not point the services at them, and do not
> add tests that run against them.**
>
> Everything that touches the chain now runs **Team A's real contracts**: their
> deployed bytecode, replayed at their Galileo addresses by
> `scripts/ops/install_team_a_local.py`, using the ABIs in `team_a_assets/` and the
> addresses in `deployments/galileo.json`. The compose stack installs them through
> the `team-a-contracts` service, and `tests/conftest.py` installs them for the
> test suite.

These Solidity stubs mirror Team A's *interface* only — `TaskCreated` /
`TaskFunded` field names, `submitProofs`, `MAX_PROOF_BATCH`, `ProofSubmitted`.
They deliberately omit Team A's authorisation and lifecycle rules: no Prover
allow-list, no bid/accept flow, no task status transitions, no escrow release.
Green tests against these stubs therefore proved the ABI shape and nothing else,
and `submitProofs` against Team A's `taskWorker`/bid flow stayed unproven.

They are kept only as a record of what the interface looked like before Team A
delivered. Nothing in the repository imports, builds or runs them; `chain/` is the
local Hardhat project, and it has no Solidity sources at all because it runs
Team A's deployed bytecode instead of compiling a stand-in.
