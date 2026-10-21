# Mocks

**Purpose:** stubs for components owned by other teams, kept only as a record of
the interface we developed against.

> **Our stub contracts are retired. Do not use them.**
>
> `mocks/legacy_mock_contracts/` is archived Solidity that mirrors Team A's ABI
> shape but not their authorisation or lifecycle rules. The event listener, proof
> submitter, compose stack, smoke test and test suite all run **Team A's real
> contracts** now — see below.

## Team A's contracts (the only chain we run)

- ABIs: `team_a_assets/*.abi.json`
- Addresses: `deployments/galileo.json`
- Runtime bytecode replayed onto a local node: `scripts/ops/install_team_a_local.py`
- Local Hardhat project: `chain/`
- Create/fund a task on it: `python -m scripts.ops.trigger_team_a_task`

Team A hand over ABIs and deployed addresses, not Solidity source, so their
contracts are not compiled here. `team-a-contracts` in `docker-compose.yml` (and
`tests/conftest.py` for the test suite) installs their deployed bytecode at the
Galileo addresses with `hardhat_setCode` and reproduces the constructor state, so
the local stack enforces Team A's real rules instead of a stand-in's.

## Team C's agent payloads

Expected payloads for Team C's agent interactions are documented in
`docs/INTEGRATION_CONTRACTS.md` and modelled in `common/models.py`.
