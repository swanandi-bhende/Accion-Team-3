"""0G Storage backend: model binaries go to 0G Storage through the official CLI.

The ``0g-storage-client`` binary builds the Merkle tree, submits the Flow transaction signed
with the key, and uploads the segments to storage nodes. This adapter runs that binary rather
than re-implementing the protocol, so the chain and storage rules stay in one maintained tool.

A model's CID is ``zg:<root hash>``. The prefix keeps 0G references apart from transaction
hashes, which have the same 32-byte shape. The CLI takes the signing key as an argument, so it is
visible to other processes on the host while an upload runs.
"""

import asyncio
import re
import tempfile
from pathlib import Path

import httpx
from eth_account import Account

from common.exceptions import ConfigurationError, StorageUnavailableError
from common.interfaces import StorageBackend

CID_PREFIX = "zg:"
ROOT_PATTERN = re.compile(r"root = (0x[0-9a-fA-F]{64})")
WEI_PER_0G = 10**18


class ZeroGStorageBackend(StorageBackend):
    """Upload and download through ``0g-storage-client``.

    An outage (the CLI or the RPC unreachable) is reported as ``StorageUnavailableError``, so the
    model registry queues and retries it. A wallet that cannot pay for an upload is a
    ``ConfigurationError``, which the registry does not retry: retrying cannot fund the wallet.
    """

    def __init__(
        self,
        cli: str,
        rpc_url: str,
        indexer_url: str,
        private_key: str,
        min_balance_0g: float = 0.1,
        timeout_s: float = 900.0,
    ):
        self.cli = cli
        self.rpc_url = rpc_url
        self.indexer_url = indexer_url
        self.private_key = private_key
        self.address = Account.from_key(private_key).address
        self.min_balance_wei = int(min_balance_0g * WEI_PER_0G)
        self.timeout_s = timeout_s

    async def balance_wei(self) -> int:
        """The wallet's balance on the RPC chain, in wei.

        Raises:
            StorageUnavailableError: If the RPC cannot be read.
        """
        body = {"jsonrpc": "2.0", "id": 1, "method": "eth_getBalance", "params": [self.address, "latest"]}
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(self.rpc_url, json=body)
                response.raise_for_status()
                result: str = response.json()["result"]
        except (httpx.HTTPError, KeyError, ValueError) as error:
            raise StorageUnavailableError(f"0G balance check failed: {error}") from error
        return int(result, 16)

    async def upload(self, data: bytes) -> str:
        """Upload ``data`` and return its ``zg:`` CID.

        Raises:
            ConfigurationError: If the wallet holds less than the minimum needed to pay for uploads.
            StorageUnavailableError: If the RPC, the CLI or the upload itself fails, or times out.
            ValueError: If the CLI split the file into fragments, which models never need.
        """
        balance = await self.balance_wei()
        if balance < self.min_balance_wei:
            raise ConfigurationError(
                f"0G wallet {self.address} holds {balance / WEI_PER_0G} 0G, below the "
                f"{self.min_balance_wei / WEI_PER_0G} 0G minimum; fund it or lower ZEROG_MIN_BALANCE"
            )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "model.bin"
            path.write_bytes(data)
            output = await self._run(
                "upload", "--url", self.rpc_url, "--key", self.private_key, "--indexer", self.indexer_url, "--file", str(path)
            )
        if "fragments" in output:
            raise ValueError("0G upload was split into fragments; a model must fit in one root")
        match = ROOT_PATTERN.search(output)
        if match is None:
            raise StorageUnavailableError("0G upload finished without reporting a root hash")
        return CID_PREFIX + match.group(1)

    async def retrieve(self, cid: str) -> bytes:
        """Download the bytes stored under a ``zg:`` CID.

        Raises:
            ValueError: If ``cid`` is not a 0G CID.
            StorageUnavailableError: If the CLI fails, times out, or writes no file.
        """
        if not cid.startswith(CID_PREFIX):
            raise ValueError(f"not a 0G Storage CID: {cid}")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "model.bin"
            await self._run("download", "--indexer", self.indexer_url, "--root", cid.removeprefix(CID_PREFIX), "--file", str(path))
            if not path.exists():
                raise StorageUnavailableError(f"0G download wrote no file for {cid}")
            return path.read_bytes()

    async def _run(self, *args: str) -> str:
        """Run the CLI and return its combined output. The signing key is redacted from any output."""
        process = await asyncio.create_subprocess_exec(
            self.cli, *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
        )
        try:
            output, _ = await asyncio.wait_for(process.communicate(), self.timeout_s)
        except TimeoutError:
            process.kill()
            await process.wait()
            raise StorageUnavailableError(f"0g-storage-client {args[0]} timed out after {self.timeout_s:.0f} s") from None
        text = output.decode(errors="replace").replace(self.private_key, "<redacted>")
        if process.returncode != 0:
            raise StorageUnavailableError(f"0g-storage-client {args[0]} failed with exit {process.returncode}: {text[-500:]}")
        return text
