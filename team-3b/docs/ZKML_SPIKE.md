# zkML Toolchain Spike

## Findings

This time-boxed V1.7 spike evaluated EZKL, Circom plus snarkjs, and a
JAX/TensorFlow-native option under the repository's Docker-oriented setup.

| Candidate | Local setup | Model fit | Transformer-small outlook | Deployment risk |
| --- | --- | --- | --- | --- |
| EZKL | Installed and importable: `ezkl 23.0.5`, `onnx 1.23.0`, `onnxruntime 1.30.0` | Strong candidate for ONNX linear/MLP/small CNN models | Unconfirmed; likely high proving cost | Requires model-specific benchmarks and proving-key setup |
| Circom + snarkjs | `circom` and `snarkjs` binaries are present | Manual circuit work is required for each model family | Poor near-term fit without substantial circuit engineering | High integration and artifact-management overhead |
| JAX/TensorFlow-native zkML | No candidate dependency was present | No repository-compatible prototype completed | Unconfirmed | High ecosystem and packaging uncertainty |

## Real EZKL benchmark

The real EZKL pipeline was run successfully against a tiny 2-layer ONNX MLP.
Reproduce it with:

```text
venv/bin/python scripts/ezkl_mlp_benchmark.py
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
`data/zkml_spike/benchmark_result.json`. These measurements are for a tiny MLP
only; transformer-small feasibility is still unmeasured.

## Decision and risk

EZKL is the provisional production standardization candidate because its ONNX
workflow best matches linear, MLP, and small-CNN models with less manual circuit
authoring than Circom. The accepted risk is that proving-key generation,
operator support, and transformer-small proving may make the 120-second
requirement unrealistic for larger models.

The repository still uses `development-commitment` as an explicit fallback
backend for API and privacy-boundary tests. Its bytes are a deterministic
commitment, not a zero-knowledge proof, and must not be submitted on-chain or
described as independently verifiable zkML. Production promotion remains gated
on transformer-small benchmarking and inspecting real serialized artifacts and
logs for weight leakage.
