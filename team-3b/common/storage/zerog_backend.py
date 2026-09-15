from common.interfaces import StorageBackend
from common.exceptions import StorageUnavailableError

class ZeroGStorageBackend(StorageBackend):
    """
    0G Storage backend adapter.
    
    NOTE: 0G Storage credentials and endpoint configurations are required for this module.
    Once Team A provides the integration endpoints and credentials for 0G, configure them 
    in .env and pass them to this constructor.
    """
    def __init__(self, endpoint: str, credentials: str):
        self.endpoint = endpoint
        self.credentials = credentials

    async def upload(self, data: bytes) -> str:
        raise NotImplementedError("0G Storage upload not yet implemented pending credentials.")

    async def retrieve(self, cid: str) -> bytes:
        raise NotImplementedError("0G Storage retrieve not yet implemented pending credentials.")
