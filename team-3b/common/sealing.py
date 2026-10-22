"""Seal model bytes with AES-GCM in the layout the enclave opens.

A sealed blob is a 12-byte random nonce followed by the AES-GCM ciphertext and its tag,
which is what ``EnclaveSim.decrypt`` reads. The registry stores the blob, and the hash of
the plaintext model is registered alongside it.
"""

import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

NONCE_BYTES = 12


def seal(plaintext: bytes, key: bytes) -> bytes:
    """Encrypt ``plaintext`` under a 256-bit ``key`` with a fresh random nonce."""
    nonce = os.urandom(NONCE_BYTES)
    return nonce + AESGCM(key).encrypt(nonce, plaintext, None)
