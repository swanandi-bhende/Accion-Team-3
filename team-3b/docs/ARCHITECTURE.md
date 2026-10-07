# Architecture Notes

## zkML Proofs in Plain Language

The proof-generator boundary receives an execution receipt and model metadata.
For a supported architecture, a production zkML backend will compile or load a
circuit, use the model weights as a private witness, and expose the input hash,
output hash, and committed model hash as public inputs. A verifier can then
check that the committed model was run on the committed input and produced the
committed output without receiving the model weights.

That proof does **not** show that the model is good, accurate, safe, unbiased,
or trained on legitimate data. It also does not prove that the input was a
truthful representation of the outside world. It proves a bounded computation
against the exact model and inputs committed by the protocol.

The current repository contains an explicitly labeled development-commitment
backend while EZKL is evaluated. It is useful for testing the API and data
flow, but it is not a cryptographic zkML proof and is not suitable for
production settlement. See `ZKML_SPIKE.md` for the toolchain evaluation and
`TEE_NOTES.md` for the hardware-attestation status.

## Federated Learning Orchestration

`federated_learning/orchestrator.py` coordinates federated-learning rounds with
the Flower framework. It builds a Flower `FedAvg` strategy, distributes the
current global model to registered clients each round, aggregates the client
updates, and records a `RoundProvenance` entry per round. The final aggregated
model is stored through the Model Registry, which returns a CID the rest of the
pipeline can consume.

Key behaviors:

- **Aggregation.** FedAvg is the default strategy. `_build_strategy` selects it
  from an explicit registry and raises `ValueError` for unknown names rather
  than silently defaulting, so a config typo cannot masquerade as a normal run.
  The registry also offers Byzantine-robust options (`fedmedian`,
  `fedtrimmedavg`, `krum`) and a differential-privacy option (`dp_fedavg`).
  `weighted_average` is an independent reference implementation of the FedAvg
  arithmetic, tested directly so the framework default is not taken on faith.
- **Fault tolerance.** The strategy uses `min_fit_clients=1`: a round proceeds
  with whatever subset of clients responded within `round_timeout`, and the
  specific clients that timed out are logged by ID so a straggler is
  diagnosable after the fact. Malformed or wrong-shaped updates are rejected and
  excluded rather than crashing aggregation.
- **Zero responders.** If no client responds in a round, the run fails with
  `NoClientsRespondedError`. `min_fit_clients=1` means "at least one"; with zero
  updates the global model is undefined, so we fail loudly instead of silently
  advancing a stale model.
- **Provenance.** Each `RoundProvenance` record (round number, participating
  client count, strategy, duration, resulting model hash) is persisted to Redis
  as the round completes, so a crash on round 7 of 10 still leaves a durable
  record of rounds 1-6.

### Current scope: clients are simulated; a real transport is tested, SuperLink is not

In the default path clients are **simulated locally**:
`federated_learning/simulated_client.py` provides real
`flwr.client.NumPyClient` instances, so Flower's own client code paths are
exercised, but they train on a synthetic regression dataset partitioned across
clients rather than real participant data.

There are two network paths on top of that:

- `federated_learning/network_runner.py` runs Flower's **gRPC** transport with a
  real server process and real client processes over sockets. It is exercised in
  `tests/integration/test_fl_real_network.py` (a 3-client, multi-round job whose
  loss must fall, and a run that survives a client process death mid-training).
  Flower 1.39 deprecates this API in favour of SuperLink, so it is the
  development and verification transport, not the chosen production one.
- `federated_learning/server_app.py` / `client_app.py` use Flower's **SuperLink**
  model for multi-host production and capture provenance inside
  `ProvenanceStrategy`. This path has **not** been validated end-to-end here —
  that requires a SuperLink runtime.

The remaining production gaps are:

1. validate the SuperLink deployment in staging (transport + round timing);
2. client authentication and authorization so only approved participants can
   contribute updates;
3. TLS and quota/rate governance for the transport; and
4. cryptographic **secure aggregation** so the server never observes an
   individual participant's raw weights (robust strategies and DP are already
   available; they are integrity-oriented, not confidentiality-oriented).

Behaviour under real network latency, packet loss, or many clients is unmeasured:
the gRPC runs above were taken on loopback with three clients.

See `FEDERATED_LEARNING.md` for deployment steps, the strategy table, and the
secure-aggregation roadmap.

### How the output feeds the rest of the pipeline

A completed training job serializes the aggregated weights, uploads them via
`ModelRegistry.upload_model`, and registers them under the task's `model_id`.
That CID is then immediately usable by the Inference Engine and TEE Runtime:
an integration test trains a model here and confirms it is retrievable, its
hash matches, and it executes through the attested inference path. From there
the same model flows into zkML proof generation and on-chain settlement like
any other registered model.
