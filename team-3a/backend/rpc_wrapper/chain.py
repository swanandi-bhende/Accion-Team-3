"""Web3 access layer for the RPC wrapper (Requirement 7)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from web3 import Web3

from backend.config import REPO_ROOT, settings


class ChainError(Exception):
    """A call or submission was rejected by the node or the contract."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def load_abi(contract_name: str) -> list[dict[str, Any]]:
    """Reads the ABI out of the Hardhat artifact tree.

    Artifacts are the single source of truth for the ABI, so a recompiled contract
    cannot drift from what this service exposes.
    """
    artifact_dir = REPO_ROOT / "artifacts" / "contracts"
    matches = list(artifact_dir.rglob(f"{contract_name}.json")) if artifact_dir.exists() else []
    matches = [m for m in matches if not m.name.endswith(".dbg.json")]
    if matches:
        return json.loads(matches[0].read_text())["abi"]
    # Containers without a compile step fall back to the committed bundle, which CI
    # keeps in sync with the contracts.
    exported = REPO_ROOT / "exports" / f"{contract_name}.abi.json"
    if exported.exists():
        return json.loads(exported.read_text())
    raise FileNotFoundError(
        f"No ABI for {contract_name}. Run `npx hardhat compile` or `npm run export:abi`."
    )


class ChainClient:
    def __init__(
        self,
        provider_url: str | None = None,
        addresses: dict[str, str] | None = None,
        read_timeout: float | None = None,
    ):
        url = provider_url or settings.web3_provider_url
        # Two connections: submissions get the general timeout, reads the 2s budget of
        # Req 7.3. Contract objects are bound to the read connection.
        self.w3 = Web3(Web3.HTTPProvider(url, request_kwargs={"timeout": settings.rpc_timeout_seconds}))
        read_kwargs = {
            "request_kwargs": {
                "timeout": read_timeout if read_timeout is not None else settings.rpc_read_timeout_seconds
            }
        }
        try:
            # web3 >= 7 silently retries timed-out calls (default 5x with backoff), which
            # would turn the 2s budget of Req 7.3 into ~12s. Reads must fail fast instead.
            provider = Web3.HTTPProvider(url, exception_retry_configuration=None, **read_kwargs)
        except TypeError:  # web3 6.x has no retry configuration to disable
            provider = Web3.HTTPProvider(url, **read_kwargs)
        self.w3_read = Web3(provider)
        self._addresses = addresses
        self._contracts: dict[str, Any] = {}

    @property
    def addresses(self) -> dict[str, str]:
        if self._addresses is None:
            self._addresses = settings.load_addresses()
        return self._addresses

    def is_connected(self) -> bool:
        try:
            return self.w3.is_connected()
        except Exception:
            return False

    def contract(self, name: str):
        if name not in self._contracts:
            address = self.addresses.get(name)
            if address is None:
                raise ChainError("UnknownContract", f"{name} is not in the deployment manifest")
            self._contracts[name] = self.w3_read.eth.contract(
                address=Web3.to_checksum_address(address), abi=load_abi(name)
            )
        return self._contracts[name]

    def read(self, contract_name: str, method: str, params: list[Any]) -> Any:
        contract = self.contract(contract_name)
        fn = getattr(contract.functions, method, None)
        if fn is None:
            raise ChainError("UnknownMethod", f"{contract_name} has no method {method}")
        try:
            return fn(*params).call()
        except Exception as exc:
            raise _classify(exc) from exc

    def submit(self, signed_tx: str) -> str:
        """Forwards an already-signed transaction (Req 7.4).

        The service never holds keys — it only relays. That is deliberate: a wrapper
        that signed on the caller's behalf would need custody of private keys.
        """
        try:
            tx_hash = self.w3.eth.send_raw_transaction(signed_tx)
            return _hex0x(tx_hash)
        except Exception as exc:
            raise ChainError("TransactionRejected", str(exc)) from exc


    # --- helpers for Team 3c and the storage flow -------------------------------

    def build_call(self, contract_name: str, method: str, params: list[Any]) -> dict[str, str]:
        """Returns unsigned {to, data} calldata for a contract method.

        The wrapper never signs. Callers sign this with their own wallet and send the
        result through /submit.
        """
        contract = self.contract(contract_name)
        fn = getattr(contract.functions, method, None)
        if fn is None:
            raise ChainError("UnknownMethod", f"{contract_name} has no method {method}")
        try:
            encode = getattr(contract, "encode_abi", None) or contract.encodeABI  # web3 v7 / v6
            data = encode(method, args=params) if hasattr(contract, "encode_abi") else encode(
                fn_name=method, args=params
            )
        except Exception as exc:
            raise ChainError("BadParams", str(exc)) from exc
        return {"to": contract.address, "data": data}

    def network_config(self) -> dict[str, Any]:
        manifest = self.addresses
        return {
            "chain_id": self.w3_read.eth.chain_id,
            "addresses": {k: manifest[k] for k in CONTRACT_KEYS if k in manifest},
            "treasury": manifest.get("Treasury"),
            "token": {
                "address": manifest.get("Token"),
                "symbol": manifest.get("TokenSymbol"),
                "decimals": manifest.get("TokenDecimals"),
            },
            "deployed_at_block": manifest.get("deployedAtBlock"),
            "events": {
                "TaskCreated": "TaskManager",
                "TaskFunded": "EscrowContract",
                "TaskSettled": "SettlementContract",
                "ProofSubmitted": "TaskManager",
                "ProofRejected": "TaskManager",
                "BidPlaced": "TaskManager",
                "BidAccepted": "TaskManager",
                "ArtifactRecorded": "TaskManager",
            },
        }

    def task_view(self, task_id: str) -> dict[str, Any]:
        """One-call task summary: record, funding, worker, proof, artifact (Team 3c results)."""
        tm = self.contract("TaskManager").functions
        try:
            task = tm.getTask(task_id).call()
            if int(task[0], 16) == 0:
                raise ChainError("TaskNotFound", f"No task {task_id}")
            proof = tm.getProof(task_id).call()
            return {
                "task_id": task_id,
                "creator": task[0],
                "description_hash": to_jsonable(task[1]),
                "deadline": task[2],
                "bounty_amount": str(task[3]),
                "status": ["Open", "InProgress", "Completed", "Settled"][task[4]],
                "worker": tm.taskWorker(task_id).call(),
                "escrow_balance": str(
                    self.contract("EscrowContract").functions.getBalance(task_id).call()
                ),
                "proof": None
                if proof[4] == 0
                else {
                    "proof_hash": to_jsonable(proof[0]),
                    "model_cid": proof[1],
                    "result_hash": to_jsonable(proof[2]),
                    "submitter": proof[3],
                    "submitted_at": proof[4],
                },
                "artifact_hash": tm.artifactHash(task_id).call() or None,
            }
        except ChainError:
            raise
        except Exception as exc:
            raise _classify(exc) from exc


def _classify(exc: Exception) -> "ChainError":
    """Timeouts get their own code so callers can tell 'slow node' from 'contract reverted'."""
    import requests

    if isinstance(exc, (requests.exceptions.Timeout, TimeoutError)):
        return ChainError("ReadTimeout", f"node did not answer within the read budget: {exc}")
    return ChainError("CallReverted", str(exc))


def _hex0x(value: bytes) -> str:
    """0x-prefixed hex regardless of web3 version (v8 dropped the prefix from .hex())."""
    h = bytes(value).hex()
    return h if h.startswith("0x") else "0x" + h


CONTRACT_KEYS = (
    "Token", "EscrowContract", "StakingContract", "IdentityContract", "TaskManager",
    "SettlementContract",
)


def to_jsonable(value: Any) -> Any:
    """Converts web3 return types (bytes, tuples, HexBytes) into JSON-safe values."""
    if isinstance(value, bytes):
        return "0x" + value.hex()
    if isinstance(value, (list, tuple)):
        return [to_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {k: to_jsonable(v) for k, v in value.items()}
    return value
