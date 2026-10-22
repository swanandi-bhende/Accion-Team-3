import asyncio
import ctypes
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from common.exceptions import ModelExecutionError
from common.interfaces import Enclave
from inference.sandbox import frame

ROOT = Path(__file__).resolve().parents[1]


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
        """Run an ONNX model on one encoded input in a new child process.

        The child is ``inference.sandbox``, so each request has its own memory and
        interpreter. The child is killed if this call is cancelled, for example when the
        engine's timeout fires, so no process outlives a timed-out request.

        Args:
            model: Decrypted ONNX model bytes.
            input_data: Input tensor encoded as ``.npy`` bytes.

        Returns:
            The first output of the model, encoded as ``.npy`` bytes.

        Raises:
            ModelExecutionError: If the bytes are not a runnable ONNX model, the input does
                not match the model, or the runtime fails.
        """
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "inference.sandbox",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=ROOT,
        )
        try:
            stdout, stderr = await proc.communicate(input=frame(model) + frame(input_data))
        except asyncio.CancelledError:
            if proc.returncode is None:
                proc.kill()
            await proc.wait()
            raise
        if proc.returncode != 0:
            detail = stderr.decode(errors="replace").strip() or f"exit status {proc.returncode}"
            raise ModelExecutionError(detail)
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
