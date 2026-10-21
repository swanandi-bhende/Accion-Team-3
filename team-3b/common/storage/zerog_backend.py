from common.interfaces import StorageBackend


class ZeroGStorageBackend(StorageBackend):
    """0G Storage backend adapter.

    NOTE: 0G Storage credentials and endpoint configurations are required for
    this module. Once Team A provides the integration endpoints and credentials
    for 0G, configure them in .env and pass them to this constructor.
    """

    def __init__(self, endpoint: str, credentials: str):
        self.endpoint = endpoint
        self.credentials = credentials

    async def upload(self, data: bytes) -> str:
        """Upload data to 0G Storage. Not implemented yet.

        Raises:
            NotImplementedError: Always, until 0G endpoints and credentials are provided.
        """
        raise NotImplementedError("0G Storage upload not yet implemented pending credentials.")

    async def retrieve(self, cid: str) -> bytes:
        """Retrieve data from 0G Storage by CID. Not implemented yet.

        Raises:
            NotImplementedError: Always, until 0G endpoints and credentials are provided.
        """
        raise NotImplementedError("0G Storage retrieve not yet implemented pending credentials.")
