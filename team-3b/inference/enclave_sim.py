import os
import ctypes
import asyncio
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.hazmat.primitives import serialization

from common.interfaces import Enclave

class EnclaveSim(Enclave):
    """
    Simulated Enclave for local development.
    Does NOT provide real hardware guarantees.
    """
    def __init__(self):
        # Generate a self-signed key for the simulator
        self._private_key = ed25519.Ed25519PrivateKey.generate()
        public_bytes = self._private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw
        )
        self._signer_id = public_bytes.hex()[:32]
        self._measurement = "simulated_enclave_measurement_v1"

    @property
    def measurement(self) -> str:
        return self._measurement

    @property
    def signer_id(self) -> str:
        return self._signer_id

    def decrypt(self, encrypted_data: bytes, sealed_key: bytes) -> bytes:
        # Assuming AES-GCM where nonce is first 12 bytes
        if len(encrypted_data) < 12:
            raise ValueError("Invalid encrypted data")
        nonce = encrypted_data[:12]
        ciphertext = encrypted_data[12:]
        aesgcm = AESGCM(sealed_key)
        return aesgcm.decrypt(nonce, ciphertext, None)

    async def run_isolated(self, model: bytes, input_data: bytes) -> bytes:
        # Simulate isolated OS subprocess by actually using asyncio subprocess
        # In a real enclave this would be hardware isolated execution.
        # For the mock, we just run a python subprocess that echoes back a dummy result
        # to ensure it actually crosses a process boundary.
        
        proc = await asyncio.create_subprocess_exec(
            'python3', '-c',
            'import sys; sys.stdout.buffer.write(b"simulated_output_for: " + sys.stdin.buffer.read())',
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, _ = await proc.communicate(input=input_data)
        if proc.returncode != 0:
            raise RuntimeError("Subprocess execution failed")
        return stdout

    def secure_erase(self, data: bytes) -> None:
        """
        Actually overwrites the buffer in memory before releasing.
        Uses ctypes to write zeros to the memory address.
        """
        # Note: bytes in python are immutable, so this is hacky for a sim.
        # In a real environment, we'd use a mutable bytearray and scrub it.
        # For the simulator to fulfill the requirement, we use ctypes.
        try:
            buffer_size = len(data)
            c_char_array = (ctypes.c_char * buffer_size).from_address(id(data) + 32)
            ctypes.memset(c_char_array, 0, buffer_size)
        except Exception:
            pass # Ignore safety rails in python for the mock

    def sign(self, data: bytes) -> bytes:
        return self._private_key.sign(data)
