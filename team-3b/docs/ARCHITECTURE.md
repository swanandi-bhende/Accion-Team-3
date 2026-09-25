# zkML Proofs in Plain Language

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

The current repository contains an explicitly labeled development commitment
backend while EZKL is evaluated. It is useful for testing the API and data
flow, but it is not a cryptographic zkML proof and is not suitable for
production settlement.# zkML In Plain Language

The proof generator receives an execution receipt and the committed model
metadata. A real zkML backend should produce a proof that a committed model was
executed on an input represented by `input_hash`, and that the resulting output
matches `output_hash`. The model weights are private witness data; the verifier
gets only the public commitments and the verification key reference needed to
check the claim.

That guarantee has clear boundaries. It does not prove that the model is good,
accurate, unbiased, safe, or useful. It does not prove that the training data
was legitimate, representative, or legally obtained. It does not prove that
the model architecture was wisely chosen. It only proves the particular
execution statement encoded by the circuit and its public inputs.

This repository currently exposes that boundary through a development
commitment backend while the team selects and measures a real zkML toolchain.
The development artifact is useful for testing data flow and API contracts,
but it is not a cryptographic zkML proof and must not be treated as one.