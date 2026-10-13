from abc import ABC, abstractmethod

class WalletManager(ABC):
    @abstractmethod
    async def get_wallet_address(self, agent_id: str) -> str | None:
        raise NotImplementedError
