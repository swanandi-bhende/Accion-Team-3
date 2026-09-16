# Current TEE Implementation Status

**Status: SIMULATION MODE**

The Verifiable AI Execution Subsystem is currently running the TEE (Trusted Execution Environment) in **Simulation Mode** (`EnclaveSim`).

## What this means:
- The system behaves functionally identical to a real hardware TEE.
- It encrypts/decrypts models, runs isolated subprocesses, and generates cryptographically signed attestation reports (using a self-signed Ed25519 keypair).
- **CRITICAL:** It does NOT provide real hardware guarantees. Simulation output must not be mistaken for real hardware attestation during demos or audits.

## What is required for Production (Hardware TEE):
To move from Simulation to a Production TEE (Intel SGX or AMD SEV), the following pieces are required:

1. **Hardware-Provisioned Keys:** Real sealing keys mapped to the CPU's root of trust.
2. **Production Enclave Build Pipeline:** Building the actual model inference code using an SGX SDK (like Gramine, Occlum, or Mystikos) or configuring AMD SEV-SNP confidential VMs.
3. **Remote Attestation Service:** Integration with Intel IAS/DCAP or AMD's attestation endpoints to verify the hardware quote.
4. **Adapter Integration:** 
   - We must build `SGXEnclave` and/or `SEVEnclave` classes implementing the `common.interfaces.Enclave` ABC.
   - We must update `inference.tee_runtime.TEERuntime._create_enclave()` to instantiate these real hardware adapters instead of `EnclaveSim` when `TEE_MODE=sgx` or `sev` is configured.
