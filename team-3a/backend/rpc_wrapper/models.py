"""Pydantic request/response schemas for the RPC wrapper (Requirement 7)."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

# Req 7.6: the contract name is validated against a closed set before anything is
# forwarded to the chain, so an arbitrary address can never be called through here.
ContractName = Literal[
    "EscrowContract",
    "IdentityContract",
    "SettlementContract",
    "StakingContract",
    "TaskManager",
    "Token",
]


class ReadRequest(BaseModel):
    contract: ContractName
    method: str = Field(min_length=1)
    params: list[Any] = Field(default_factory=list)


class ReadResponse(BaseModel):
    contract: ContractName
    method: str
    result: Any


class TransactionRequest(BaseModel):
    signed_tx: str = Field(min_length=4, description="0x-prefixed raw signed transaction")


class TransactionResponse(BaseModel):
    tx_hash: str


class ErrorResponse(BaseModel):
    code: str
    message: str
