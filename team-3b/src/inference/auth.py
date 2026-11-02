"""Wallet authentication for job requests: a signed, single-use challenge.

A client asks for a challenge for ``(task_id, wallet)``, then signs the message it gets back
(EIP-191 ``personal_sign``). The message carries the nonce and the expiry. A job is accepted
only if the nonce is live and unused, the signature recovers to the wallet, and the wallet is
the worker Team A's TaskManager records for the task.
"""

import json
import secrets
import time
from collections.abc import Awaitable, Callable
from typing import Any

from eth_account import Account
from eth_account.messages import encode_defunct
from pydantic import BaseModel, Field

CHALLENGE_TTL_S = 120
NONCE_KEY = "auth:nonce:{}"

TaskWorkerLookup = Callable[[str], Awaitable[str | None]]


class AuthError(Exception):
    """A request failed authentication. ``status_code`` is the HTTP status to report."""

    def __init__(self, code: str, detail: str, status_code: int):
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.status_code = status_code


class ChallengeRequest(BaseModel):
    task_id: str = Field(pattern=r"^(0x)?[0-9a-fA-F]{64}$")
    wallet_address: str = Field(pattern=r"^0x[0-9a-fA-F]{40}$")


class Authorization(BaseModel):
    """The signed challenge that accompanies a job request."""

    wallet_address: str = Field(pattern=r"^0x[0-9a-fA-F]{40}$")
    nonce: str = Field(pattern=r"^[0-9a-f]{32}$")
    expires_at: int
    signature: str = Field(pattern=r"^0x[0-9a-fA-F]{130}$")


def challenge_message(task_id: str, wallet: str, nonce: str, expires_at: int) -> str:
    return f"Accion job authorization\nTask: {task_id}\nWallet: {wallet}\nNonce: {nonce}\nExpires: {expires_at}"


class Challenges:
    """Issues challenges and checks signed authorizations. Nonces live in Redis."""

    def __init__(self, redis_client: Any, lookup: TaskWorkerLookup | None, clock: Callable[[], float] = time.time):
        self.redis = redis_client
        self.lookup = lookup
        self.clock = clock

    async def issue(self, task_id: str, wallet: str) -> dict[str, Any]:
        task_id = "0x" + task_id.lower().removeprefix("0x")
        wallet = wallet.lower()
        nonce = secrets.token_hex(16)
        expires_at = int(self.clock()) + CHALLENGE_TTL_S
        record = {"task_id": task_id, "wallet": wallet, "expires_at": expires_at}
        await self.redis.set(NONCE_KEY.format(nonce), json.dumps(record), ex=CHALLENGE_TTL_S)
        return {
            "nonce": nonce,
            "expires_at": expires_at,
            "message": challenge_message(task_id, wallet, nonce, expires_at),
        }

    async def verify(self, task_id: str, auth: Authorization) -> None:
        """Raise ``AuthError`` unless ``auth`` is a live, correctly signed challenge from the task's worker.

        The nonce is consumed first, so a challenge gives one attempt whether or not it succeeds.
        """
        task_id = "0x" + task_id.lower().removeprefix("0x")
        wallet = auth.wallet_address.lower()
        raw = await self.redis.getdel(NONCE_KEY.format(auth.nonce))
        if raw is None:
            raise AuthError("challenge_unknown", "challenge is unknown, expired or already used", 401)
        record = json.loads(raw.decode() if isinstance(raw, bytes) else raw)
        if record["task_id"] != task_id or record["wallet"] != wallet or record["expires_at"] != auth.expires_at:
            raise AuthError("challenge_mismatch", "authorization does not match its challenge", 401)
        if self.clock() >= auth.expires_at:
            raise AuthError("challenge_expired", "challenge has expired", 401)

        message = challenge_message(task_id, wallet, auth.nonce, auth.expires_at)
        try:
            signer = Account.recover_message(encode_defunct(text=message), signature=auth.signature)
        except Exception as error:
            raise AuthError("bad_signature", "signature is malformed", 401) from error
        if signer.lower() != wallet:
            raise AuthError("bad_signature", "signature does not match the wallet", 401)

        if self.lookup is None:
            raise AuthError("auth_unavailable", "no chain is configured to check the task's worker", 503)
        try:
            worker = await self.lookup(task_id)
        except Exception as error:
            raise AuthError("auth_unavailable", f"could not read the task's worker: {type(error).__name__}", 503) from error
        if worker is None or worker.lower() != wallet:
            raise AuthError("not_task_worker", "wallet is not the worker recorded for this task", 403)
