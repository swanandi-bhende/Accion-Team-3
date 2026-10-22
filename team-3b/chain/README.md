# Local chain running Team A's contracts

A Hardhat node that runs **Team A's deployed bytecode**, not our own Solidity.

Team A hand over ABIs and deployed addresses, not source, so their contracts
cannot be compiled locally. `scripts/ops/install_team_a_local.py` instead copies
the runtime code the contracts have on 0G Galileo (recorded in
`team_a_assets/bytecode.json`, with provenance: chain, block, fetch time) onto
this node with `hardhat_setCode`, at the same addresses, then reproduces the
constructor state that lives in storage and wires the contracts to each other
exactly as Team A did.

The node runs with `chainId: 16602` — Galileo's chain id — so the same
environment profile (`WEB3_CHAIN_ID=16602`, the Galileo addresses, the ABI
exports in `team_a_assets/`) works against the local node and the real testnet.

There is intentionally no `solidity` section: nothing here is compiled.

```bash
docker compose up -d hardhat-node      # node only
python -m scripts.ops.install_team_a_local   # install + wire the contracts
python -m scripts.ops.trigger_team_a_task    # create and fund one task
```

`npm install` runs on first start (`node_modules/` is git-ignored).

## Refreshing the bytecode

```bash
python scripts/ops/fetch_team_a_bytecode.py
```

Re-fetches from the public Galileo RPC and rewrites `team_a_assets/bytecode.json`.
Do this whenever Team A deploys a new version, then diff the record in
`deployments/galileo.json`.
