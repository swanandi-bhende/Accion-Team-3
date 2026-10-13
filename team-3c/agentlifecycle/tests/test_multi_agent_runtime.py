import asyncio

import redis.asyncio as redis

from agents.models import AgentDefinition
from registry.redis_registry import RedisAgentRegistry
from runtime.runtime import AgentRuntime


REDIS_URL = "redis://localhost:6379"


async def main():

    # ==================================================
    # 1. CONNECT TO REDIS
    # ==================================================

    client = redis.from_url(
        REDIS_URL,
        decode_responses=True
    )

    await client.ping()

    print("Redis connected.")

    registry = RedisAgentRegistry(client)


    # ==================================================
    # 2. CREATE THREE TEST AGENTS
    # ==================================================

    agents = [

        AgentDefinition(
            agent_id="agent_001",
            name="ResearchAgent",
            capabilities=[
                "text_analysis",
                "data_analysis"
            ],
            configuration={
                "decision_interval": 2
            },
            wallet_address="0x111"
        ),

        AgentDefinition(
            agent_id="agent_002",
            name="CodeAgent",
            capabilities=[
                "code_analysis",
                "programming"
            ],
            configuration={
                "decision_interval": 2
            },
            wallet_address="0x222"
        ),

        AgentDefinition(
            agent_id="agent_003",
            name="DataAgent",
            capabilities=[
                "data_analysis",
                "statistics"
            ],
            configuration={
                "decision_interval": 2
            },
            wallet_address="0x333"
        )

    ]


    # ==================================================
    # 3. RESET TEST AGENTS
    # ==================================================

    print("\n--- RESETTING TEST AGENTS ---")

    for definition in agents:

        await registry.delete(
            definition.agent_id
        )

    print("Old test agents removed.")


    # ==================================================
    # 4. REGISTER ALL THREE AGENTS
    # ==================================================

    print("\n--- REGISTERING TEST AGENTS ---")

    for definition in agents:

        await registry.register(
            definition
        )

        print(
            f"Registered {definition.agent_id}"
        )


    # ==================================================
    # 5. VERIFY REGISTRY
    # ==================================================

    print("\n--- VERIFYING REGISTRY ---")

    for definition in agents:

        registered_agent = await registry.get(
            definition.agent_id
        )

        if registered_agent is None:

            raise RuntimeError(
                f"Test setup failed: "
                f"{definition.agent_id} "
                f"was not registered."
            )

        print(
            f"✓ {definition.agent_id} exists in Redis"
        )


    # ==================================================
    # 6. CREATE ONE AGENT RUNTIME
    # ==================================================

    runtime = AgentRuntime(
        registry
    )


    # ==================================================
    # 7. LOAD ALL THREE AGENTS
    # ==================================================

    print("\n--- LOADING AGENTS ---")

    for definition in agents:

        await runtime.load_agent(
            definition.agent_id
        )


    print(
        f"\nRuntime currently manages "
        f"{len(runtime.get_all_agents())} agents."
    )


    # ==================================================
    # 8. INITIALIZE ALL THREE
    # ==================================================

    print("\n--- INITIALIZING ---")

    await asyncio.gather(

        runtime.initialize_agent(
            "agent_001"
        ),

        runtime.initialize_agent(
            "agent_002"
        ),

        runtime.initialize_agent(
            "agent_003"
        )

    )


    # ==================================================
    # 9. ACTIVATE ALL THREE
    # ==================================================

    print("\n--- ACTIVATING ---")

    await asyncio.gather(

        runtime.activate_agent(
            "agent_001"
        ),

        runtime.activate_agent(
            "agent_002"
        ),

        runtime.activate_agent(
            "agent_003"
        )

    )


    # ==================================================
    # 10. LET ALL THREE RUN
    # ==================================================

    print(
        "\n--- ALL THREE AGENTS RUNNING ---"
    )

    await asyncio.sleep(5)


    # ==================================================
    # 11. PAUSE ONLY AGENT 002
    # ==================================================

    print(
        "\n--- PAUSING AGENT 002 ---"
    )

    await runtime.pause_agent(
        "agent_002"
    )

    print(
        "\nAgent 002 is now paused."
    )

    print(
        "Agents 001 and 003 should continue running."
    )

    await asyncio.sleep(4)


    # ==================================================
    # 12. CHECK INDEPENDENT STATES
    # ==================================================

    agent_001 = runtime.get_agent(
        "agent_001"
    )

    agent_002 = runtime.get_agent(
        "agent_002"
    )

    agent_003 = runtime.get_agent(
        "agent_003"
    )


    print("\n--- CURRENT STATES ---")

    print(
        "agent_001:",
        agent_001.state
    )

    print(
        "agent_002:",
        agent_002.state
    )

    print(
        "agent_003:",
        agent_003.state
    )


    # Verify that only agent 002 is paused.

    assert agent_001.state.value == "ACTIVE"

    assert agent_002.state.value == "PAUSED"

    assert agent_003.state.value == "ACTIVE"


    print(
        "\n✓ Independent lifecycle states verified."
    )


    # ==================================================
    # 13. RESUME ONLY AGENT 002
    # ==================================================

    print(
        "\n--- RESUMING AGENT 002 ---"
    )

    await runtime.resume_agent(
        "agent_002"
    )

    await asyncio.sleep(3)


    # ==================================================
    # 14. VERIFY AGENT 002 IS ACTIVE AGAIN
    # ==================================================

    agent_002 = runtime.get_agent(
        "agent_002"
    )

    assert (
        agent_002.state.value
        == "ACTIVE"
    )

    print(
        "✓ Agent 002 successfully resumed."
    )


    # ==================================================
    # 15. SHUTDOWN ALL THREE
    # ==================================================

    print(
        "\n--- SHUTTING DOWN ALL AGENTS ---"
    )

    await asyncio.gather(

        runtime.shutdown_agent(
            "agent_001"
        ),

        runtime.shutdown_agent(
            "agent_002"
        ),

        runtime.shutdown_agent(
            "agent_003"
        )

    )


    # ==================================================
    # 16. VERIFY FINAL STATES
    # ==================================================

    print(
        "\n--- VERIFYING FINAL STATES ---"
    )

    for definition in agents:

        agent = runtime.get_agent(
            definition.agent_id
        )

        assert agent is not None

        assert (
            agent.state.value
            == "INACTIVE"
        )

        print(
            f"✓ {definition.agent_id} -> "
            f"{agent.state.value}"
        )


    # ==================================================
    # 17. TEST PASSED
    # ==================================================

    print(
        "\n==================================="
    )

    print(
        "MULTI-AGENT RUNTIME TEST PASSED"
    )

    print(
        "==================================="
    )


    # ==================================================
    # 18. CLOSE REDIS
    # ==================================================

    await client.aclose()


if __name__ == "__main__":

    asyncio.run(
        main()
    )