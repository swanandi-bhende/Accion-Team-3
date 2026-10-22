# Diagrams

Rendered from Mermaid source. Open this file on GitHub, or paste a block into
<https://mermaid.live>, to see the picture.

## End-to-end execution pipeline (as implemented)

This is the flow `inference/pipeline.py` actually runs. Stage boundaries are
labelled with the decision each one makes when a stage fails, because those
decisions — not the happy path — are what keep the poll loop alive.

```mermaid
flowchart TD
    subgraph chain["Local chain (Team A's deployed contracts)"]
        TM["TaskManager.createTask"]
        ES["EscrowContract.deposit (ERC-20)"]
        TM -->|TaskCreated| EL
        ES -->|TaskFunded| EL
    end

    subgraph listener["Event Listener"]
        EL["EventListener._process_events<br/>restores task state from Redis<br/>dispatches when created AND funded"]
    end

    EL -->|"dispatch(task) — admission point"| Q

    subgraph executor["Pipeline executor (inference/pipeline.py)"]
        Q["bounded asyncio.Queue<br/>maxsize = pipeline_max_in_flight x 2"]
        Q -->|"PIPELINE_BACKPRESSURE logged when full"| W["worker pool<br/>pipeline_max_in_flight tasks"]
    end

    W --> S1

    subgraph stages["Per-task stage chain (direct awaited calls)"]
        S1["1. InferenceEngine.execute()<br/>asyncio.Semaphore(max_in_flight)<br/>ModelRegistry -> storage -> 30s timeout"]
        S1 -->|"ExecutionReceipt (execution_id)"| S2
        S2["2. TEERuntime.generate_attestation()<br/>Ed25519 signature over measurement:signer:in:out"]
        S2 --> S3
        S3["3. ZkMLProver.generate_proof()<br/>120s timeout<br/>public_inputs = input/output/model hash + architecture"]
        S3 -->|ProofArtifact| S4
        S4["4. ProofSubmitter.submit_proof()<br/>buffers; 10s batch window<br/>3 retries x 10s, then DLQ"]
    end

    S4 -->|"submitProofs(taskIds, proofHashes, modelCids, resultHashes)"| TM
    S4 -.->|"retries exhausted"| DLQ[("Redis<br/>proof_submitter:dlq")]
    S3 -.->|"proof bytes + public inputs"| S5
    S5["5. VerificationInterface.verify()<br/>zkml_snark, independent verifier"]
    S5 -.->|"result=true"| OK(["settled + independently verified"])

    classDef drop fill:#fee,stroke:#c00;
    classDef seam fill:#eef,stroke:#33c,stroke-dasharray: 4 3;

    DLQ:::drop
```

## Failure decisions per stage boundary

```mermaid
flowchart LR
    start(["stage boundary"]) --> q{"which failure?"}

    q -->|"ModelNotFoundError"| d1["log + drop<br/>dropped_model_not_found"]
    q -->|"TimeoutExceededError (30s)"| d2["log + drop<br/>dropped_inference_timeout<br/>(retry only re-blows the deadline)"]
    q -->|"IntegrityCheckFailedError"| d3["log + drop<br/>dropped_integrity_failure"]
    q -->|"TEEUnavailableError / StorageUnavailableError"| d4["bounded retry 3x, backoff 0.5s,1s<br/>then drop<br/>dropped_tee_unavailable"]
    q -->|"TimeoutError (120s proof)"| d5["log + drop<br/>dropped_proof_timeout<br/>(other tasks keep flowing)"]
    q -->|"UnsupportedArchitectureError"| d6["log + drop<br/>dropped_unsupported_architecture"]
    q -->|"submit exhausted retries"| d7["dead-letter to Redis<br/>dropped_submission_failed<br/>next task still processed"]

    classDef drop fill:#fee,stroke:#c00;
    class d1,d2,d3,d4,d5,d6,d7 drop
```

## Step 17 seam: the same shape as separate containers

```mermaid
flowchart LR
    subgraph now["Now (one process, dev + integration)"]
        L1["EventListener"] --> Q1["asyncio.Queue"] --> P1["worker pool<br/>stage chain"]
    end

    subgraph next["Step 17 (multi-container)"]
        L2["event-listener<br/>container"] -->|"XADD"| R[("Redis Streams<br/>task queue")]
        R -->|"XREADGROUP"| P2["pipeline worker<br/>container"]
        P2 -->|"existing REST/Redis calls"| C2["inference / proof-submitter<br/>containers"]
    end

    Q1 -.->|"swapped for, same contract:<br/>enqueue + bounded admission"| R
```

The queue is the only thing that changes. Because `Pipeline.enqueue` is already
the listener's dispatch target and the per-task stage chain is a plain awaited
call sequence, moving to Redis Streams replaces the transport without touching
stage logic.
