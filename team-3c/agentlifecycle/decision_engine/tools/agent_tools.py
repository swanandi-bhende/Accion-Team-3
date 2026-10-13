class AgentToolInterface:
    async def get_agent_context(self, agent_id: str):
        raise NotImplementedError
