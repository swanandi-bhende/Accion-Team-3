"""Redis implementation of the Agent Registry."""

from agents.models import AgentDefinition
from registry.interface import AgentRegistry


class RedisAgentRegistry(AgentRegistry):
    """Persist Market Agent definitions in Redis."""

    KEY_PREFIX = "agent:"

    def __init__(self, redis_client) -> None:
        self.redis = redis_client

    def _key(self, agent_id: str) -> str:
        return f"{self.KEY_PREFIX}{agent_id}"

    async def register(self, agent: AgentDefinition) -> None:
        key = self._key(agent.agent_id)

        if await self.redis.exists(key):
            raise ValueError(
                f"Agent already registered: {agent.agent_id}"
            )

        await self.redis.set(
            key,
            agent.model_dump_json()
        )

    async def get(
        self,
        agent_id: str
    ) -> AgentDefinition | None:

        raw = await self.redis.get(
            self._key(agent_id)
        )

        if raw is None:
            return None

        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")

        return AgentDefinition.model_validate_json(raw)

    async def list(self) -> list[AgentDefinition]:
        agents: list[AgentDefinition] = []

        async for key in self.redis.scan_iter(
            match=f"{self.KEY_PREFIX}*"
        ):
            raw = await self.redis.get(key)

            if raw is None:
                continue

            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")

            agents.append(
                AgentDefinition.model_validate_json(raw)
            )

        return agents

    async def update(
        self,
        agent: AgentDefinition
    ) -> None:

        key = self._key(agent.agent_id)

        if not await self.redis.exists(key):
            raise ValueError(
                f"Agent is not registered: {agent.agent_id}"
            )

        await self.redis.set(
            key,
            agent.model_dump_json()
        )

    async def delete(self, agent_id: str) -> None:
        await self.redis.delete(
            self._key(agent_id)
        )