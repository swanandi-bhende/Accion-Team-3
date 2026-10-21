# Tests

**Purpose:** Contains all unit, property, and integration tests.
- `/unit/`: Standard unit tests.
- `/property/`: Hypothesis-based property tests strictly mapped to the design document properties.
- `/integration/`: End-to-end pipeline wiring tests, including the chain tests that run against Team A's contracts.
- `chain_helper.py`: starts a local Hardhat node and installs Team A's deployed bytecode on it (Node 20+ required for the chain tests).
- `conftest.py`: shared fixtures; `team_a_chain` installs and wires Team A's contracts once per module.

The chain tests never run against our retired stubs (`mocks/legacy_mock_contracts/`)
— see `mocks/README.md`.
