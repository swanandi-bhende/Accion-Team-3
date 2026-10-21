"""Fetch Team A's deployed runtime bytecode from the public Galileo RPC.

ABI-only handoffs cannot be deployed locally; the runtime code at their addresses
can be replayed on a local chain with `hardhat_setCode`.
"""

import json
import time
import urllib.request
from pathlib import Path
from typing import Any

RPC = "https://evmrpc-testnet.0g.ai"
ADDRESSES = {
    "TEST_TOKEN": "0x22374BC6FF12FF2Bf407Da332F16Ee65D52CE0EC",
    "EscrowContract": "0x5B685Ca03A8072FB3f9721Dd7017Ed0D8C719217",
    "StakingContract": "0x41eCc7485d2Bbc188B4F638871ca3f00D2c65C38",
    "IdentityContract": "0xC70c8865eACC340AB15Af8d4e726F438CD639Db9",
    "TaskManager": "0xA55f34e4137aD55f48aB268d4361F2225C4B5078",
    "SettlementContract": "0x45D1a7AF404D2EDf5E5c23C7c0b6338227b9BC4E",
}


def rpc(method: str, params: list[Any], timeout: int = 30) -> Any:
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    req = urllib.request.Request(RPC, data=body, headers={"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        payload = json.load(response)
    if "error" in payload:
        raise RuntimeError(f"{method}: {payload['error']}")
    return payload["result"]


def main() -> None:
    chain_id = rpc("eth_chainId", [])
    block = rpc("eth_getBlockByNumber", ["latest", False])
    out = {
        "network": "0G Galileo testnet",
        "chain_id": int(chain_id, 16),
        "source_rpc": RPC,
        "source_block": int(block["number"], 16),
        "source_block_hash": block["hash"],
        "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "contracts": {},
    }
    for name, address in ADDRESSES.items():
        code = rpc("eth_getCode", [address, "latest"])
        if code in ("0x", "0x0"):
            raise SystemExit(f"{name}: no code at {address}")
        out["contracts"][name] = {"address": address, "runtime_bytecode": code}
        print(f"{name} {address} runtime={len(code)//2 - 1} bytes")
    destination = Path(__file__).resolve().parents[2] / "team_a_assets" / "bytecode.json"
    destination.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {destination}")


if __name__ == "__main__":
    main()
