# Internal Checkpoints

## V1.6 Core Loop Review

Properties 1-5, 19, and 20 now have explicit Hypothesis or unit coverage. In
plain language, they guarantee that successful inference returns a complete
receipt with correct input/output hashes (Property 1), concurrent requests
remain isolated (Property 2), unknown model identifiers fail before execution
and audit logging (Property 3), model bytes are verified before entering the
TEE and decrypted bytes are securely erased (Property 4), attestation reports
contain the expected identity and hashes (Property 5), registry lookups return
the registered version and CID (Property 19), and a CID must resolve to the
same bytes that were registered, including when the referenced content is
tampered with (Property 20).

The local coverage run used:

```text
pytest --cov=inference --cov=common --cov-report=term-missing -q -p no:warnings
```

It produced 27 passing tests. The component results were Inference Engine
100%, TEE Runtime 93%, and Model Registry 98%. The combined `inference` and
`common` total was 76%, with unimplemented or integration-only storage/config
paths accounting for most of the remaining lines. The error branches for
`ModelNotFoundError`, `TimeoutExceededError`, `TEEUnavailableError`,
`IntegrityCheckFailedError`, and `StorageUnavailableError` are each exercised
by at least one test.

The concurrency property uses an `asyncio.Event` barrier to force request B to
enter while request A is paused. The integrity property spies on
`secure_erase`, and the CID property covers both unresolved and tampered
content. Focused edge tests cover empty input, unusual model identifiers, the
exact 500 MB boundary, the 30.0-second timeout configuration, and reconnecting
the registry after closing its Redis client.

The current limitations are deliberate: the TEE remains a simulator rather
than hardware-backed SGX/SEV, storage integrations remain mocked in tests, and
the Redis restart test skips when no local Redis service is available. CI
provides a real Redis 7 service container and runs the unit/property gate on
every push and pull request. Proof generation remains gated on this checkpoint.

No commit or tag was created during this session. After reviewing the CI run,
the team can create the requested marker with:

```text
git tag checkpoint-1-core-loop
```