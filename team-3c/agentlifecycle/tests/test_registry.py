import redis.asyncio as redis

from agents.models import AgentDefinition, AgentState
from registry.redis_registry import RedisAgentRegistry


REDIS_URL = "redis://localhost:6379"


async def create_registry():
    client = redis.from_url(
        REDIS_URL,
        decode_responses=True
    )

    await client.ping()

    return client, RedisAgentRegistry(client)


async def main():
    client, registry = await create_registry()

    print("Redis connected successfully.")

    # --------------------------------------------------
    # 1. Create an AgentDefinition
    # --------------------------------------------------

    agent = AgentDefinition(
        agent_id="agent_001",
        name="ResearchAgent",
        capabilities=[
            "text_analysis",
            "data_analysis"
        ],
        configuration={
            "decision_interval": 10
        },
        wallet_address="0x123456789",
        state=AgentState.REGISTERED
    )

    print("\nCreated agent:")
    print(agent)

    # --------------------------------------------------
    # 2. Register the agent
    # --------------------------------------------------

    # Clean up from a previous test run.
    await registry.delete(agent.agent_id)

    await registry.register(agent)

    print("\nAgent registered successfully.")

    # --------------------------------------------------
    # 3. Retrieve the agent
    # --------------------------------------------------

    loaded_agent = await registry.get("agent_001")

    print("\nAgent retrieved from Redis:")
    print(loaded_agent)

    # --------------------------------------------------
    # 4. Verify the data survived storage
    # --------------------------------------------------

    assert loaded_agent is not None
    assert loaded_agent.agent_id == "agent_001"
    assert loaded_agent.name == "ResearchAgent"
    assert loaded_agent.capabilities == [
        "text_analysis",
        "data_analysis"
    ]
    assert loaded_agent.wallet_address == "0x123456789"

    print("\n✓ Agent data is correct.")

    # --------------------------------------------------
    # 5. List agents
    # --------------------------------------------------

    agents = await registry.list()

    print("\nRegistered agents:")

    for registered_agent in agents:
        print(
            f"- {registered_agent.agent_id}: "
            f"{registered_agent.name}"
        )

    # --------------------------------------------------
    # 6. Update the agent
    # --------------------------------------------------

    agent.state = AgentState.ACTIVE

    await registry.update(agent)

    updated_agent = await registry.get("agent_001")

    assert updated_agent is not None
    assert updated_agent.state == AgentState.ACTIVE

    print("\n✓ Agent updated successfully.")
    print("Current state:", updated_agent.state)

    # --------------------------------------------------
    # 7. Delete the agent
    # --------------------------------------------------

    await registry.delete("agent_001")

    deleted_agent = await registry.get("agent_001")

    assert deleted_agent is None

    print("\n✓ Agent deleted successfully.")

    # --------------------------------------------------
    # Finished
    # --------------------------------------------------

    await client.aclose()

    print("\n===================================")
    print("REGISTRY TEST PASSED")
    print("===================================")


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())