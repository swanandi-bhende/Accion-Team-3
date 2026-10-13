import asyncio

import redis.asyncio as redis

from agents.models import AgentDefinition
from registry.redis_registry import RedisAgentRegistry
from runtime.runtime import AgentRuntime


REDIS_URL = "redis://localhost:6379"


async def main():

    # --------------------------------------------------
    # 1. Connect to Redis
    # --------------------------------------------------

    client = redis.from_url(
        REDIS_URL,
        decode_responses=True
    )

    await client.ping()

    print("Redis connected.")


    # --------------------------------------------------
    # 2. Create Registry
    # --------------------------------------------------

    registry = RedisAgentRegistry(client)


    # --------------------------------------------------
    # 3. Make sure our test agent exists
    # --------------------------------------------------

    agent_definition = AgentDefinition(
        agent_id="agent_001",
        name="ResearchAgent",
        capabilities=[
            "text_analysis",
            "data_analysis"
        ],
        configuration={
            "decision_interval": 5
        },
        wallet_address="0x123456789"
    )

    existing_agent = await registry.get("agent_001")

    if existing_agent is None:

        await registry.register(agent_definition)

        print("Test agent registered.")

    else:

        print("Test agent already exists.")


    # --------------------------------------------------
    # 4. Create Agent Runtime
    # --------------------------------------------------

    runtime = AgentRuntime(registry)


    # --------------------------------------------------
    # 5. Load agent from Redis
    # --------------------------------------------------

    agent = await runtime.load_agent("agent_001")

    print(
        f"\nLoaded agent: {agent.agent_id}"
    )

    print(
        f"Initial state: {agent.state}"
    )


    # --------------------------------------------------
    # 6. Initialize agent
    # --------------------------------------------------

    await runtime.initialize_agent("agent_001")

    print(
        f"After initialization: {agent.state}"
    )


    # --------------------------------------------------
    # 7. Activate agent
    # --------------------------------------------------

    await runtime.activate_agent("agent_001")

    print(
        f"After activation: {agent.state}"
    )


    # --------------------------------------------------
    # 8. Let decision cycle run
    # --------------------------------------------------

    print("\nLetting decision cycle run...")

    await asyncio.sleep(6)


    # --------------------------------------------------
    # 9. Pause agent
    # --------------------------------------------------

    await runtime.pause_agent("agent_001")

    print(
        f"After pause: {agent.state}"
    )


    # --------------------------------------------------
    # 10. Resume agent
    # --------------------------------------------------

    await runtime.resume_agent("agent_001")

    print(
        f"After resume: {agent.state}"
    )


    # --------------------------------------------------
    # 11. Let it run again
    # --------------------------------------------------

    await asyncio.sleep(3)


    # --------------------------------------------------
    # 12. Shutdown
    # --------------------------------------------------

    await runtime.shutdown_agent("agent_001")

    print(
        f"After shutdown: {agent.state}"
    )


    # --------------------------------------------------
    # 13. Verify final state
    # --------------------------------------------------

    assert agent.state.value == "INACTIVE"

    print("\n===================================")
    print("RUNTIME TEST PASSED")
    print("===================================")


    # --------------------------------------------------
    # 14. Close Redis connection
    # --------------------------------------------------

    await client.aclose()


if __name__ == "__main__":
    asyncio.run(main())