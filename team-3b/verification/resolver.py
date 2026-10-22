"""By-value and by-reference proof resolution."""

import json
from typing import Any


class ProofResolutionError(Exception):
    """Raised when a proof reference cannot be resolved."""


class StorageProofResolver:
    """Resolve proof references from storage or from submitted transactions.

    CIDs (``Qm`` or ``bafy`` prefixes) are read from ``storage``. ``0x`` transaction
    hashes are decoded only when both ``web3`` and ``contract`` are configured.
    """

    def __init__(self, storage: Any, web3: Any = None, contract: Any = None):
        self.storage = storage
        self.web3 = web3
        self.contract = contract

    async def resolve(self, reference: str) -> bytes:
        """Fetch the proof bytes that ``reference`` points to.

        Returns:
            The stored bytes for a CID, or JSON-encoded proof hashes for a
            ``submitProofs`` transaction.

        Raises:
            ProofResolutionError: If the reference is unsupported, missing, or cannot be decoded.
        """
        if reference.startswith("Qm") or reference.startswith("bafy"):
            try:
                content: bytes = await self.storage.retrieve(reference)
                return content
            except Exception as error:
                raise ProofResolutionError(f"Unable to resolve CID: {reference}") from error

        if reference.startswith("0x") and self.web3 is not None:
            try:
                receipt = self.web3.eth.get_transaction_receipt(reference)
                if receipt is None:
                    raise ProofResolutionError(f"Transaction not found: {reference}")
                transaction = self.web3.eth.get_transaction(reference)
                return self._decode_transaction_proof(transaction)
            except ProofResolutionError:
                raise
            except Exception as error:
                raise ProofResolutionError(f"Unable to resolve transaction: {reference}") from error

        raise ProofResolutionError(f"Unsupported proof reference: {reference}")

    def _decode_transaction_proof(self, transaction: Any) -> bytes:
        if self.contract is None:
            raise ProofResolutionError("No contract decoder configured")
        try:
            function, arguments = self.contract.decode_function_input(transaction["input"])
            if function.fn_name != "submitProofs":
                raise ProofResolutionError("Transaction is not submitProofs")
            return json.dumps(
                {"transaction_proof_hashes": [value.hex() for value in arguments["proofHashes"]]},
                sort_keys=True,
            ).encode()
        except ProofResolutionError:
            raise
        except Exception as error:
            raise ProofResolutionError("Unable to decode submitted proof transaction") from error
