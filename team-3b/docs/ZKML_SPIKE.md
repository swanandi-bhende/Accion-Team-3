# zkML Toolchain Spike

## Findings

This time-boxed V1.7 spike evaluated EZKL, Circom plus snarkjs, and a
JAX/TensorFlow-native option under the repository's Docker-oriented setup.

| Candidate | Local setup | Model fit | Transformer-small outlook | Deployment risk |
| --- | --- | --- | --- | --- |
| EZKL | Installed and importable: `ezkl 23.0.5`, `onnx 1.23.0`, `onnxruntime 1.30.0` | Real tiny fixtures verified for all four supported architectures | Larger-model scaling remains unmeasured | Requires production-sized model benchmarks |
| Circom + snarkjs | `circom` and `snarkjs` binaries are present | Manual circuit work is required for each model family | Poor near-term fit without substantial circuit engineering | High integration and artifact-management overhead |
| JAX/TensorFlow-native zkML | No candidate dependency was present | No repository-compatible prototype completed | Unconfirmed | High ecosystem and packaging uncertainty |

## Real EZKL benchmark

The real EZKL pipeline was run successfully against a tiny 2-layer ONNX MLP.
Reproduce it with:

```text
venv/bin/python scripts/zkml/ezkl_mlp_benchmark.py
```

Measured with EZKL 23.0.5 on this Apple Silicon development machine:

| Stage | Time |
| --- | ---: |
| Settings generation | 0.041 s |
| Circuit compilation | 0.002 s |
| SRS generation | 14.19 s |
| Circuit setup | 8.15 s |
| Witness generation | 0.036 s |
| Proof generation | 11.29 s |
| Verification | 0.076 s |

The proof verified successfully. Artifact sizes were:

- Proof: 17,650 bytes
- Proving key: 553,912,075 bytes
- Verification key: 263,431 bytes
- SRS: 16,777,476 bytes
- Witness: 772 bytes

The full machine-readable result is in
`data/zkml_spike/benchmark_result.json`. All four tiny fixtures verified:

| Architecture | Prove | Verify | Proof |
| --- | ---: | ---: | ---: |
| linear | 9.07 s | 0.065 s | 17,666 B |
| mlp | 9.36 s | 0.069 s | 17,612 B |
| cnn | 10.00 s | 0.060 s | 19,835 B |
| transformer_small | 37.01 s | 0.168 s | 42,623 B |

These measurements use tiny fixtures; production-sized model scaling remains
unmeasured.

The scheduled workflow `.github/workflows/zkml-regression.yml` reruns this
benchmark weekly and checks that verification remains true, proof generation
stays below 120 seconds, proof time stays within a 2x baseline envelope, and
proof size stays within a 25% envelope.

## Decision and risk

EZKL is the provisional production standardization candidate because its ONNX
workflow best matches linear, MLP, and small-CNN models with less manual circuit
authoring than Circom. The accepted risk is that proving-key generation,
operator support, and transformer-small proving may make the 120-second
requirement unrealistic for larger models. The tiny transformer_small fixture
is currently under the limit, but that is not evidence that a production-sized
transformer will meet it.

The repository still uses `development-commitment` as an explicit fallback
backend for API and privacy-boundary tests. Its bytes are a deterministic
commitment, not a zero-knowledge proof, and must not be submitted on-chain or
described as independently verifiable zkML. Production promotion remains gated
on production-sized model benchmarking and inspecting real serialized artifacts
and logs for weight leakage.

## Proof size: spec and toolchain disagree

**Status: proposed, not ratified.** Option A, a bound of at most 64 KiB, is engineering's
proposal. The spec owner has not accepted it. As written, it does not meet Property 7,
which requires `proof_bytes` to be constant regardless of model size. The binding
requirement is Property 7 (validates 3.6). Requirement 3.2 is about verification time,
not proof bytes. The "~256 bytes" figure appears only in the design section. The full
analysis and the questions for the spec owner are in `docs/PROOF_SIZE_DECISION.md`.

The design document describes the zkML proof as "constant-size ~256 bytes", and
Property 7 requires the proof size to be constant regardless of model size. The
measurements disagree:

| Architecture | EZKL proof (bytes) | Development commitment (bytes) |
| --- | ---: | ---: |
| linear | 17,666 | 78 |
| mlp | 17,612 | 78 |
| cnn | 19,835 | 78 |
| transformer_small | 42,623 | 78 |

The EZKL numbers come from `data/zkml_spike/benchmark_result.json`. The development
commitment is a 14-byte tag plus two SHA-256 digests (`proof_generator/verifier.py`).

Why both cannot hold:

- About 256 bytes is the size of a succinct pairing proof such as Groth16 on BN254.
  EZKL emits a KZG-based halo2 proof, and at these model sizes that is 17–43 KB.
- The development commitment is constant at 78 bytes, so it meets the size wording.
  It is not zero-knowledge, so it does not meet the purpose.

Options:

- **A. Amend Property 7 to a bound.** Constant for a given architecture and at most 64 KiB,
  and keep EZKL. This changes the spec. The measured sizes already differ by architecture. The chain stores only a hash of the proof (`proof_hash` in
  `submitProofs`), so the chain cost does not change. The 17–43 KB cost falls on
  storage and on the verifier's transfer.
- **B. Keep about 256 bytes.** Wrap the EZKL proof in a recursive or Groth16 proof.
  This is a new toolchain step, and no path has been measured.
- **C. Keep the target and record a written deviation.**

Recommendation: C until the spec owner decides, then A if they amend Property 7. EZKL is
still provisional. The 256-byte figure is not supported by any measured path. The 64 KiB
bound has been checked only on the four tiny fixtures.

What the code enforces:

- `tests/unit/test_zkml_proof_size.py` checks the recorded EZKL proofs against the
  64 KiB bound. A regenerated benchmark that exceeds it fails.
- Property 7 passes for the development backend because two development proofs have
  equal length. It does not test any size target, and the development proof is not
  zero-knowledge.

## Development proofs stay off chain

A development-commitment proof is a deterministic commitment, not a zero-knowledge
proof. Its hash must not reach `submitProofs`, where it would look like a verified zkML
proof. The proof submitter therefore refuses any proof whose `backend` is not in
`PRODUCTION_PROOF_BACKENDS` (`common/constants.py`), which is empty until a production
backend is wired in:

- Default: refused with `ProofNotSubmittableError`. The pipeline records the task as
  `dropped_proof_not_submittable`, and nothing is sent to the chain.
- A proof with no backend named is refused, even when the development opt-in is set.
- `PROOF_ALLOW_DEVELOPMENT_BACKEND=true` (or `ProofSubmitter(allow_development_backend=True)`)
  is the explicit test opt-in. The on-chain tests set it explicitly, and no deployment
  sets it.

Consequence: the local compose stack no longer settles tasks, because no production
backend exists yet. The smoke test reports the refusal as a step 5 failure.

## Proving key size and where keys live

The first EZKL run produced a 553 MB proving key for the tiny linear model. Measured
again with `scripts/zkml/ezkl_key_size.py` on the same fixture:

| Configuration | logrows | inner columns | Proving key | Verification key | Proof | Verifies |
| --- | ---: | ---: | ---: | ---: | ---: | :---: |
| Default (spike) | 17 | 2 | 553.9 MB | 263 KB | 17.7 KB | yes |
| EZKL resource calibration | 15 | 2 | 138.5 MB | 67 KB | 17.7 KB | yes |
| Calibration, forced logrows | 12 | 1 | 24.1 MB | 6.7 KB | 17.5 KB | yes |
| Calibration, forced logrows | 11 | 1 | 25.6 MB | 4.0 KB | 24.5 KB | yes |

The default `logrows` 17 comes from a 2^16-entry lookup table; the fixed-point scale
does not change it, and hand-set lookup ranges make EZKL panic. Calibration fixes the
table, and forcing logrows to 12 with one inner column is the smallest size that
verifies cleanly. Below 12 the key stops shrinking.

Policy. The first two rules are in force now. The last applies when EZKL is wired into
the pipeline, which it is not yet:

- **Keys never go in an image.** `.dockerignore` excludes `data/`, and no Dockerfile
  copies it. The base image holds no keys.
- **Keys never go in git.** The proving key lives under `ZKML_PROVING_KEY_STORE`.
- **A key is published and pinned.** It goes to the model storage backend under its
  content hash. A service loads it on start, verifies the hash, and caches it on a volume.
  Image size and cold start do not depend on the key size.

Caveat: these are tiny fixtures. The key grows with the circuit, so a production-sized
model needs its own measurement under the same script before the policy is trusted.

## The EZKL backend

`ZKML_BACKEND=ezkl` makes the pipeline and the verification API use EZKL. The default,
`development`, stays for tests and for hosts without EZKL. Only `ezkl` proofs are
zero-knowledge, so only they can reach the chain.

How it works:

- **Bundles, built ahead of time.** `scripts/zkml/build_proving_bundles.py` builds a bundle
  per seeded model under `ZKML_PROVING_KEY_STORE/<model_hash>/`. A bundle holds the
  settings, the compiled circuit, the verification and proving keys, and the SRS. A task
  never waits for key generation. The build tries EZKL's calibrated circuit size and a few
  smaller ones, and keeps the one with the smallest proving key that proves and verifies a
  calibration input.
- **Staging, then rename.** A bundle is built in a staging directory and renamed into
  place, so an interrupted build never leaves a partial bundle that could be loaded.
- **Child processes.** Every EZKL operation runs in `proof_generator/ezkl_worker.py`, a
  child process. ezkl's native threads abort the interpreter at exit (a libc++ mutex
  error). That abort was seen in the service and the test process. The child writes its
  result and then exits with `os._exit`, so the teardown never runs. A cancelled proof kills
  its child, as the inference sandbox does.
- **Output check.** The circuit computes on fixed-point numbers, so its output differs from
  the TEE's float output by a few steps of `2**-quantization_scale`. The prover refuses a
  proof whose output is further away than `EZKL_OUTPUT_TOLERANCE_STEPS` (16) steps. It
  records the task as `dropped_proof_inconsistent`.
- **Verification.** The verification API loads the bundle for the artifact's `model_hash`
  and checks that the artifact's `zk_instances_hash` matches the proof, the architecture
  matches the bundle, and EZKL accepts the proof. A `model_hash` with anything other than
  letters and digits is refused before any path is built from it.

What a verifier learns, and what it does not:

- It learns that the model's circuit ran on *some* input and produced the output in the
  proof's instances. The proof is zero-knowledge, so the weights are not revealed.
- It does **not** learn from the proof alone that `input_hash` and `output_hash` describe
  those instances. The pipeline checks that link when it proves: the input bytes must hash
  to the receipt's `input_hash`, and the circuit's output must match the TEE's output
  within the tolerance. A third party who holds only the hashes cannot re-derive the link.
  Closing that gap needs the circuit to commit to the hashes, which is a later change.

Operations:

- **Platform.** `ezkl==23.0.5` has an x86_64 wheel but no linux/arm64 wheel. On an Apple
  Silicon Docker host the images keep the development backend, so compose does not settle
  tasks there. To turn settlement on, build the images on a linux/amd64 host with
  `INSTALL_ZKML=true`, build the bundles, and set `ZKML_BACKEND=ezkl`. On this Mac, EZKL works
  natively: `pip install -r requirements/zkml.txt` in the host venv.
- **Stale bundles.** Bundles built before the `quantization_scale` field existed are
  refused with a message that names the directory to delete and rebuild.
- **Key distribution is not finished.** A bundle is a local directory, so every verifier
  needs the same bundle. Publishing bundles to the storage backend under their hash, and
  pinning that hash, is the next step. Until then compose mounts the store read-only.
- **Scope.** Measured and tested on the tiny linear and MLP models. A production-sized model
  needs its own key-size measurement (`scripts/zkml/ezkl_key_size.py`) before its bundle is
  trusted.
