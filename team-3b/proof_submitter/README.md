# Proof Submitter

**Purpose:** Manages the generation of zero-knowledge proofs (via zkML) and submits execution proofs (TEE attestations and zkML proofs) along with model CIDs to Team 3a's smart contracts on-chain. Handles batching, retries, and dead-letter queuing.

**Requirements Mapped:**
- Requirement 3: Zero-Knowledge Machine Learning Proof Generation
- Requirement 6: Execution Proof Submission to Blockchain

The submitter batches proofs for 10 seconds, retries failed transactions three
times with 10-second intervals, and persists exhausted batches to the Redis
`proof_submitter:dlq` list. The contract address is configured through
`PROOF_SUBMITTER_CONTRACT_ADDRESS` so the mock TaskManager can be replaced by
Team A's deployment without changing submission logic.
