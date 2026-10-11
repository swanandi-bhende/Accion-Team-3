import httpx

from common.exceptions import StorageUnavailableError
from common.interfaces import StorageBackend


class IPFSBackend(StorageBackend):
    def __init__(self, node_url: str = "http://localhost:5001"):
        self.node_url = node_url.rstrip("/")

    async def upload(self, data: bytes) -> str:
        url = f"{self.node_url}/api/v0/add"
        try:
            async with httpx.AsyncClient() as client:
                files = {"file": ("model.bin", data)}
                response = await client.post(url, files=files, timeout=30.0)
                response.raise_for_status()
                result = response.json()
                return str(result["Hash"])
        except httpx.RequestError as e:
            raise StorageUnavailableError(f"IPFS upload failed: {e}") from e

    async def retrieve(self, cid: str) -> bytes:
        url = f"{self.node_url}/api/v0/cat?arg={cid}"
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(url, timeout=30.0)
                response.raise_for_status()
                return response.content
        except httpx.RequestError as e:
            raise StorageUnavailableError(f"IPFS retrieve failed: {e}") from e
