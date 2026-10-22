# Proof size: decision for the spec owner

**Status: open, not ratified.** The current EZKL implementation does not meet Property 7 as
written. Engineering recommends a written deviation now and a spec amendment if the owner agrees.

## What the spec requires

Three places in the spec mention the zkML proof's size or verification cost. They do not say
the same thing.

| Source | Text (paraphrased where noted) | What it constrains |
| --- | --- | --- |
| Requirement 3.2 | "verifiable in constant time regardless of model size" | Verification time |
| Property 7 (validates 3.6) | "the proof_bytes size SHALL be constant regardless of model size" | Proof bytes. **Binding.** |
| Design, zkML section | "constant-size ~256 bytes" | Proof bytes. The only place 256 appears. |

So the 256-byte figure comes from the design text, not from Requirement 3.2. Property 7 is
the binding size obligation, and it is stricter than the 64 KiB bound we use.

## What we measured

From the EZKL spike (`data/zkml_spike/benchmark_result.json`, EZKL 23.0.5, tiny fixtures):

| Architecture | proof.json (bytes) | Prove time (s) | pk.key (bytes) |
| --- | ---: | ---: | ---: |
| linear | 17,666 | 9.07 | 553,912,075 |
| mlp | 17,612 | 9.36 | 553,912,075 |
| cnn | 19,835 | 10.00 | 553,912,075 |
| transformer_small | 42,623 | 37.01 | 2,240,598,451 |

## What this means

1. **Option A does not satisfy Property 7.** The measured proof sizes already differ by
   architecture, from 17,612 to 42,623 bytes (2.4×). "Constant for a given architecture" is a
   weaker statement than the spec's "constant regardless of model size".
2. **The 64 KiB bound is tested on four tiny fixtures only.** The largest is 42,623 bytes,
   about 65% of the bound. Production circuits are larger, and nothing measured shows sizes
   stay under 64 KiB.
3. **The Property 7 test does not test EZKL.** `tests/property/test_zkml_prover.py` passes
   because the development backend always emits 78 bytes. A fixed-length dummy is not evidence
   of a constant-size zkML proof.
4. **Requirement 3.2 is not measured.** Verification time per architecture has not been
   recorded.
5. **Proving-key size is a separate risk.** `pk.key` is 2.24 GB for transformer_small. The
   bundles are git-ignored and must be stored outside the repository.

## Options

- **A1. Amend Property 7 to a bound.** Proof bytes constant per architecture and at most 64 KiB.
  Keeps EZKL. Needs: the spec change, a per-architecture size test, and production-sized
  benchmarks to confirm the bound holds.
- **A2. Keep Property 7 literal.** Needs a fixed-size proof that does not depend on the model:
  a recursive aggregation step or a pairing-based SNARK wrapper, which is how the design's
  ~256 bytes would be reached. None of these paths is built or measured, and the schedule is
  unknown.
- **C. Keep Property 7 and record a written deviation.** Ship EZKL as is. The spec is not met,
  and the owner signs the deviation.

**Engineering recommendation: C now, then A1 if the owner amends Property 7.** EZKL is still
provisional, A2 is an unmeasured toolchain step, and A1 is a wording change backed by measurements.
Do not describe the current state as meeting Property 7.

## Questions for the spec owner

1. Does Property 7's "constant regardless of model size" mean constant across all models, or
   constant for each architecture?
2. If across all models: do you accept the written deviation (C), or fund the fixed-size
   wrapper work (A2) and its schedule?
3. If amending (A1): is 64 KiB the right ceiling, or should it be another number?
4. Is Requirement 3.2 a verification-time requirement? If yes, we will measure verification time
   per architecture and report it.
5. Should the design's "~256 bytes" line be amended or kept as a goal?

## What the code does now

- `tests/unit/test_zkml_proof_size.py` checks the recorded EZKL proofs against 64 KiB. It skips
  when the benchmark file is absent, which is the case in this checkout because `data/` is
  git-ignored.
- `docs/ZKML_SPIKE.md` section "Proof size: spec and toolchain disagree" now reads "proposed, not
  ratified", not "final".
- No code changes depend on the decision. Changing it later affects the test bound and the
  Property 7 test only.
