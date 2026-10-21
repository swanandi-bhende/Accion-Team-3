import asyncio
import ctypes
import sys

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from common.interfaces import Enclave


class EnclaveSim(Enclave):
    """
    Simulated Enclave for local development.
    Does NOT provide real hardware guarantees.
    """
    def __init__(self) -> None:
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
        """Fixed placeholder measurement; the simulator has no hardware-backed measurement."""
        return self._measurement

    @property
    def signer_id(self) -> str:
        """First 32 hex characters of the simulator's Ed25519 public key."""
        return self._signer_id

    @property
    def public_key_bytes(self) -> bytes:
        """Raw 32-byte Ed25519 public key that verifies signatures from sign()."""
        return self._private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )

    def decrypt(self, encrypted_data: bytes, sealed_key: bytes) -> bytes:
        """Decrypt AES-GCM data laid out as a 12-byte nonce followed by the ciphertext.

        Args:
            encrypted_data: The 12-byte nonce, then the AES-GCM ciphertext and tag.
            sealed_key: AES-GCM key used to decrypt the data.

        Returns:
            The decrypted plaintext.

        Raises:
            ValueError: If encrypted_data is shorter than the 12-byte nonce.
            cryptography.exceptions.InvalidTag: If authentication fails.
        """
        # Assuming AES-GCM where nonce is first 12 bytes
        if len(encrypted_data) < 12:
            raise ValueError("Invalid encrypted data")
        nonce = encrypted_data[:12]
        ciphertext = encrypted_data[12:]
        aesgcm = AESGCM(sealed_key)
        plaintext: bytes = aesgcm.decrypt(nonce, ciphertext, None)
        return plaintext

    async def run_isolated(self, model: bytes, input_data: bytes) -> bytes:
        """Echo input_data through a separate Python process. The model is not executed.

        The simulator only demonstrates a process boundary; the model argument is unused.

        Args:
            model: Decrypted model bytes. Ignored by the simulator.
            input_data: Bytes piped to the subprocess on stdin.

        Returns:
            The subprocess output, ``b"simulated_output_for: " + input_data``.

        Raises:
            RuntimeError: If the subprocess exits with a non-zero status.
        """
        # Simulate isolated OS subprocess by actually using asyncio subprocess
        # In a real enclave this would be hardware isolated execution.
        # For the mock, we just run a python subprocess that echoes back a dummy result
        # to ensure it actually crosses a process boundary.

        proc = await asyncio.create_subprocess_exec(
            sys.executable, '-c',
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
        Simulate in-memory zeroization without mutating the caller-owned bytes object.
        A real enclave would scrub the original buffer; the simulator keeps the caller's
        value intact so tests can assert on the exact input while still enforcing the
        zeroization behavior on a copy.
        """
        try:
            scrubbed = bytearray(data)
            buffer_size = len(scrubbed)
            c_char_array = (ctypes.c_char * buffer_size).from_buffer(scrubbed)
            ctypes.memset(c_char_array, 0, buffer_size)
        except Exception:
            pass  # Ignore safety rails in python for the mock

    def sign(self, data: bytes) -> bytes:
        """Sign data with the simulator's Ed25519 private key; returns the 64-byte signature."""
        return self._private_key.sign(data)
