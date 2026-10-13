import asyncio
from typing import Dict

from agents.models import AgentDefinition, AgentState
from registry.interface import AgentRegistry


class MarketAgent:
    """
    Runtime representation of one Market Agent.

    This class manages the lifecycle of the agent.
    It does NOT contain the actual AI intelligence yet.
    """

    def __init__(
        self,
        definition: AgentDefinition,
        registry: AgentRegistry,
    ):
        self.definition = definition
        self.registry = registry

        self.agent_id = definition.agent_id

        # Runtime state
        self.state = definition.state

        # Background decision-cycle task
        self._decision_task: asyncio.Task | None = None

        # Tells the decision cycle when it should completely stop
        self._stop_event = asyncio.Event()

        # Tells the decision cycle whether it is allowed to run
        # It starts SET, meaning the agent is not paused.
        self._resume_event = asyncio.Event()
        self._resume_event.set()

    async def _set_state(self, new_state: AgentState):
        """
        Change the runtime state AND persist the same state
        in the Agent Registry.
        """

        self.state = new_state
        self.definition.state = new_state

        await self.registry.update(self.definition)

        print(
            f"[{self.agent_id}] State -> {new_state.value}"
        )

    async def initialize(self):
        """
        Initialize the agent's dependencies.

        Actual wallet, Decision Engine and marketplace
        initialization will be added later.
        """

        if self.state != AgentState.REGISTERED:
            raise RuntimeError(
                f"Cannot initialize agent in state {self.state}"
            )

        print(f"[{self.agent_id}] Initializing...")

        await self._set_state(
            AgentState.INITIALIZING
        )

        # --------------------------------------------------
        # Future dependency initialization goes here:
        #
        # Wallet Manager
        # Decision Engine
        # Marketplace Adapter
        # etc.
        # --------------------------------------------------

        await asyncio.sleep(0.1)

        await self._set_state(
            AgentState.INACTIVE
        )

        print(
            f"[{self.agent_id}] Initialization complete."
        )

    async def activate(self):
        """
        Start the agent's autonomous decision cycle.
        """

        if self.state != AgentState.INACTIVE:
            raise RuntimeError(
                f"Cannot activate agent in state {self.state}"
            )

        print(f"[{self.agent_id}] Activating...")

        self._stop_event.clear()

        # Make sure the cycle is allowed to run.
        self._resume_event.set()

        await self._set_state(
            AgentState.ACTIVE
        )

        self._decision_task = asyncio.create_task(
            self._decision_cycle()
        )

        print(
            f"[{self.agent_id}] ACTIVE"
        )

    async def _decision_cycle(self):
        """
        Autonomous decision-cycle infrastructure.

        IMPORTANT:
        This is NOT the AI decision-making logic yet.

        Later this loop will call the Decision Engine,
        which will evaluate available marketplace tasks.

        It does NOT execute Team 3B workloads.
        """

        while not self._stop_event.is_set():

            # --------------------------------------------------
            # If the agent is paused, wait here.
            #
            # The cycle will NOT perform decision work while
            # this event is cleared.
            # --------------------------------------------------

            await self._resume_event.wait()

            # Shutdown may have happened while we were waiting.
            if self._stop_event.is_set():
                break

            # --------------------------------------------------
            # PLACEHOLDER FOR FUTURE DECISION ENGINE
            # --------------------------------------------------

            print(
                f"[{self.agent_id}] "
                "Decision cycle running..."
            )

            await asyncio.sleep(2)

    async def pause(self):
        """
        Pause the decision cycle without destroying the agent.
        """

        if self.state != AgentState.ACTIVE:
            raise RuntimeError(
                f"Cannot pause agent in state {self.state}"
            )

        print(f"[{self.agent_id}] Pausing...")

        # Stop the decision cycle from performing new work.
        self._resume_event.clear()

        await self._set_state(
            AgentState.PAUSED
        )

        print(
            f"[{self.agent_id}] PAUSED"
        )

    async def resume(self):
        """
        Resume a paused decision cycle.
        """

        if self.state != AgentState.PAUSED:
            raise RuntimeError(
                f"Cannot resume agent in state {self.state}"
            )

        print(f"[{self.agent_id}] Resuming...")

        # Allow the decision cycle to continue.
        self._resume_event.set()

        await self._set_state(
            AgentState.ACTIVE
        )

        print(
            f"[{self.agent_id}] ACTIVE"
        )

    async def shutdown(self):
        """
        Gracefully stop the agent and its background cycle.
        """

        if self.state not in (
            AgentState.ACTIVE,
            AgentState.PAUSED,
        ):
            raise RuntimeError(
                f"Cannot shutdown agent in state {self.state}"
            )

        print(
            f"[{self.agent_id}] Shutting down..."
        )

        await self._set_state(
            AgentState.STOPPING
        )

        # Tell the decision cycle to stop.
        self._stop_event.set()

        # If the agent is paused, the decision cycle may
        # currently be waiting on _resume_event.
        #
        # Setting this releases it so shutdown can finish.
        self._resume_event.set()

        if self._decision_task is not None:
            await self._decision_task
            self._decision_task = None

        await self._set_state(
            AgentState.INACTIVE
        )

        print(
            f"[{self.agent_id}] INACTIVE"
        )


class AgentRuntime:
    """
    Manages multiple MarketAgent instances.

    The Runtime is responsible for orchestration and
    lifecycle management.
    """

    def __init__(
        self,
        registry: AgentRegistry,
    ):
        self.registry = registry

        self.agents: Dict[str, MarketAgent] = {}

    async def load_agent(
        self,
        agent_id: str,
    ) -> MarketAgent:
        """
        Load an existing agent definition from Redis
        and create its runtime representation.
        """

        if agent_id in self.agents:
            return self.agents[agent_id]

        definition = await self.registry.get(
            agent_id
        )

        if definition is None:
            raise ValueError(
                f"Agent not found in registry: {agent_id}"
            )

        agent = MarketAgent(
            definition,
            self.registry,
        )

        self.agents[agent_id] = agent

        print(
            f"[Runtime] Loaded agent: {agent_id}"
        )

        return agent

    async def initialize_agent(
        self,
        agent_id: str,
    ):
        agent = await self.load_agent(
            agent_id
        )

        await agent.initialize()

    async def activate_agent(
        self,
        agent_id: str,
    ):
        agent = await self.load_agent(
            agent_id
        )

        await agent.activate()

    async def pause_agent(
        self,
        agent_id: str,
    ):
        agent = self.agents.get(
            agent_id
        )

        if agent is None:
            raise ValueError(
                f"Agent is not loaded: {agent_id}"
            )

        await agent.pause()

    async def resume_agent(
        self,
        agent_id: str,
    ):
        agent = self.agents.get(
            agent_id
        )

        if agent is None:
            raise ValueError(
                f"Agent is not loaded: {agent_id}"
            )

        await agent.resume()

    async def shutdown_agent(
        self,
        agent_id: str,
    ):
        agent = self.agents.get(
            agent_id
        )

        if agent is None:
            raise ValueError(
                f"Agent is not loaded: {agent_id}"
            )

        await agent.shutdown()

    def get_agent(
        self,
        agent_id: str,
    ) -> MarketAgent | None:
        return self.agents.get(agent_id)

    def get_all_agents(
        self,
    ) -> list[MarketAgent]:
        return list(self.agents.values())