"""Team 3C demo: an autonomous agent deciding which tasks to pursue.

REAL components:
  - Agent Runtime (WP1): lifecycle, readiness checks, decision cycle
  - Decision Engine (WP2): shared Task Reader (Step 1), capability check in
    code, private LLM judgement (Step 2), hard policy rules, decision memory
  - LLM: Google Gemini through LangChain (your API key)
  - Capability handler: analyze_text (WP1's real local handler)

DEMO STAND-INS (clearly labelled, NOT the real components):
  - marketplace  -> stands in for WP3's simulator / Team 3A's chain
  - bidding      -> stands in for WP3's Bidding Engine; places NO bid
  - event log    -> prints events; stands in for WP4's event store
  - registry     -> in memory, so the demo needs no Redis

The two tasks carry ONLY a description, like real Team 3A tasks (on-chain
there is no capability field). One should pass the capability check and
one should fail it.

Run from the agentlifecycle folder (PowerShell):

    $env:DECISION_LLM_PROVIDER = "gemini"
    $env:GOOGLE_API_KEY = "<your key>"
    .venv\\Scripts\\python demo_decision_engine.py

Rehearsal without a key (scripted answers, no LLM is called):

    .venv\\Scripts\\python demo_decision_engine.py --offline

Gemini free-tier prompts may be used by Google to improve its products:
only these simulated tasks are sent. A normal run uses about 2 requests.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import textwrap
import time

from agents.models import AgentDefinition, AgentState
from decision_engine.factory import DecisionEngineFactory
from decision_engine.model.configuration import (
    DecisionModelConfiguration,
    ProviderConfigurationError,
)
from decision_engine.model.provider import ProviderDependencyError
from decision_engine.models.task import MarketplaceTask
from decision_engine.task_reading.catalogue import DEMO_CAPABILITY_CATALOGUE
from interfaces.bid_manager import BidManager
from interfaces.marketplace import TaskMarketplaceService
from registry.interface import AgentRegistry
from runtime.capabilities import analyze_text
from runtime.capability_registry import InMemoryCapabilityRegistry
from runtime.runtime import AgentRuntime

AGENT_ID = "text-analyst-01"
LINE = "-" * 72


def say(text: str = "", indent: int = 0) -> None:
    prefix = " " * indent
    for line in (text.splitlines() or [""]):
        print(textwrap.fill(line, width=100, initial_indent=prefix,
                            subsequent_indent=prefix + "  ") if line else "")


# ---------------------------------------------------------------------------
# Demo stand-ins (labelled)
# ---------------------------------------------------------------------------

def demo_tasks(now: float) -> list[MarketplaceTask]:
    simulation = {"environment": "simulation", "source": "demo_decision_engine.py"}
    return [
        MarketplaceTask(
            task_id="SIM-TEXT-001",
            description=(
                "Count the words and characters in a short supplied paragraph "
                "and return the statistics as JSON."
            ),
            bounty_amount=100.0,
            deadline=int(now) + 3600,
            market_context=simulation,
        ),
        MarketplaceTask(
            task_id="SIM-IMAGE-002",
            description="Generate a 1024x1024 photorealistic product image of a red shoe.",
            bounty_amount=250.0,
            deadline=int(now) + 3600,
            market_context=simulation,
        ),
    ]


class InMemoryAgentRegistry(AgentRegistry):
    """Demo stand-in for the Redis registry (same interface)."""

    def __init__(self) -> None:
        self._agents: dict[str, str] = {}

    async def register(self, agent: AgentDefinition) -> None:
        if agent.agent_id in self._agents:
            raise ValueError(f"Agent already registered: {agent.agent_id}")
        self._agents[agent.agent_id] = agent.model_dump_json()

    async def get(self, agent_id: str) -> AgentDefinition | None:
        raw = self._agents.get(agent_id)
        return None if raw is None else AgentDefinition.model_validate_json(raw)

    async def list(self) -> list[AgentDefinition]:
        return [AgentDefinition.model_validate_json(r) for r in self._agents.values()]

    async def update(self, agent: AgentDefinition) -> None:
        if agent.agent_id not in self._agents:
            raise ValueError(f"Agent is not registered: {agent.agent_id}")
        self._agents[agent.agent_id] = agent.model_dump_json()

    async def delete(self, agent_id: str) -> None:
        self._agents.pop(agent_id, None)


class DemoMarketplace(TaskMarketplaceService):
    """Demo stand-in for WP3's simulator: two open, description-only tasks."""

    def __init__(self, tasks: list[MarketplaceTask]) -> None:
        self.tasks = tasks
        self.polls = 0

    async def get_available_tasks(self, agent_id: str) -> list[MarketplaceTask]:
        self.polls += 1
        say(f"\n{LINE}\n[Marketplace stand-in] poll #{self.polls}: "
            f"{len(self.tasks)} open simulated task(s)")
        if self.polls == 1:
            for task in self.tasks:
                say(f"{task.task_id}: \"{task.description}\"", indent=2)
            say("(no capability field - only a description, like Team 3A tasks)", indent=2)
        return list(self.tasks)


class DemoBiddingStub(BidManager):
    """Demo stand-in for WP3's Bidding Engine. Places NO bid."""

    def __init__(self) -> None:
        self.received: list[str] = []

    async def submit_decision(self, decision) -> None:
        self.received.append(decision.task_id)
        say(f"[Bidding stand-in, WP3] received {decision.task_id} for pricing. "
            "No bid placed - the Bidding Engine is WP3's work.", indent=4)


class DemoDispatcher:
    """Never called: dispatch happens only after an award (WP3/WP4)."""

    async def dispatch(self, task):
        raise RuntimeError("Dispatch is not part of this demo")


class NarratingEventLog:
    """Demo stand-in for WP4's event store: prints each event as a story."""

    def __init__(self, tasks: list[MarketplaceTask], factory: DecisionEngineFactory):
        self._tasks = {t.task_id: t for t in tasks}
        self._factory = factory
        self.cycles = 0
        self.decisions = 0
        self._cycle_done = asyncio.Event()

    async def write_event(self, event) -> None:
        kind = event.get("event_type")
        if kind == "initialization_complete":
            say("Agent ready: model configured; capability 'analyze_text' has a "
                "verified handler.", indent=2)
        elif kind == "decision_evaluated":
            self.decisions += 1
            self._narrate_decision(event["decision"])
        elif kind == "decision_cycle_completed":
            self.cycles += 1
            if event["decisions_made"] == 0 and event["tasks_found"]:
                say(f"Cycle {self.cycles}: {event['tasks_found']} task(s) seen, all "
                    "already decided -> nothing re-judged, no LLM call.", indent=2)
            else:
                say(f"Cycle {self.cycles} complete: {event['tasks_found']} task(s) "
                    f"seen, {event['decisions_made']} new decision(s), "
                    f"{event['bids_submitted']} handed to bidding.", indent=2)
            self._cycle_done.set()
        elif kind == "decision_cycle_skipped":
            say(f"Temporary LLM problem ({event['error_type']}): agent stays ACTIVE, "
                f"retrying in {event['retry_in_seconds']:.0f}s.", indent=2)

    async def wait_for_cycles(self, count: int) -> None:
        while self.cycles < count:
            self._cycle_done.clear()
            await self._cycle_done.wait()

    def _narrate_decision(self, d: dict) -> None:
        task = self._tasks.get(d["task_id"])
        say(f"\nTask {d['task_id']}", indent=2)
        if task is not None:
            say(f"Description: \"{task.description}\"", indent=4)

        reading = None
        if task is not None and self._factory.task_reader is not None:
            reading = self._factory.task_reader.peek(task)
        source = d.get("requirements_source")
        if source in ("extracted", "declared", "unspecified"):
            needs = d["required_capabilities"] or ["(no catalogue capability)"]
            unsupported = [g for g in d["capability_gap"] if g not in d["required_capabilities"]]
            how = ("declared by the task (no reading needed)" if source == "declared"
                   else "LLM read the description")
            say(f"Step 1  {how} -> needs: {', '.join(needs)}", indent=4)
            if unsupported:
                say(f"        needs outside the catalogue: {', '.join(unsupported)}", indent=4)
            if reading is not None and reading.reasoning:
                say(f"        why: {reading.reasoning}", indent=4)

        reasons = d["rejection_reasons"]
        if "capability_missing" in reasons:
            say(f"Check   capability check (code): FAIL - agent lacks "
                f"{', '.join(d['capability_gap'])}", indent=4)
            say("Step 2  skipped: no private LLM call for a task the agent cannot do",
                indent=4)
        elif d.get("model_suitable") is not None:
            say("Check   capability check (code): PASS - agent has every needed "
                "capability", indent=4)
            verdict = "suitable" if d["model_suitable"] else "not suitable"
            say(f"Step 2  private LLM judgement: {verdict}, match score "
                f"{d['match_score']:.2f}", indent=4)
            if d.get("reasoning"):
                say(f"        why: {d['reasoning']}", indent=4)
        if d["suitable"]:
            say("Result  APPROVED by LLM + every hard rule -> handed to bidding", indent=4)
        else:
            say(f"Result  REJECTED ({', '.join(reasons)})", indent=4)


# ---------------------------------------------------------------------------
# Offline rehearsal model (scripted, NOT an LLM)
# ---------------------------------------------------------------------------

def offline_model():
    from decision_engine.models.decision import DecisionBatch, TaskDecision
    from decision_engine.task_reading.models import (
        TaskRequirementsAnswer,
        TaskRequirementsBatch,
    )
    from tests.fake_models import FakeStructuredChatModel

    def read(_prompt):
        return TaskRequirementsBatch(tasks=[
            TaskRequirementsAnswer(
                task_id="SIM-TEXT-001", required_capabilities=["analyze_text"],
                unsupported_requirements=[],
                reasoning="[scripted] Counting words and characters is text analysis."),
            TaskRequirementsAnswer(
                task_id="SIM-IMAGE-002", required_capabilities=[],
                unsupported_requirements=["image generation"],
                reasoning="[scripted] Producing an image is not text analysis."),
        ])

    def judge(_prompt):
        return DecisionBatch(decisions=[
            TaskDecision(task_id="SIM-TEXT-001", match_score=0.9, suitable=True,
                         reasoning="[scripted] Directly matches analyze_text."),
        ])

    return FakeStructuredChatModel(
        by_schema={TaskRequirementsBatch: read, DecisionBatch: judge}
    )


# ---------------------------------------------------------------------------
# The demo
# ---------------------------------------------------------------------------

async def run(offline: bool = False, timeout_seconds: float = 180.0) -> int:
    print(LINE)
    if offline:
        say("OFFLINE REHEARSAL - scripted answers, NO LLM is called.\n"
            "Use only to practise. The real demo needs a Gemini key.")
        factory = DecisionEngineFactory(
            offline_model(), capability_catalogue=DEMO_CAPABILITY_CATALOGUE
        )
    else:
        try:
            config = DecisionModelConfiguration.from_env()
            factory = DecisionEngineFactory.from_configuration(
                config, capability_catalogue=DEMO_CAPABILITY_CATALOGUE
            )
        except (ProviderConfigurationError, ProviderDependencyError) as exc:
            say(f"SETUP FAILED: {exc}")
            return 1
        say(f"LIVE DEMO - provider: {config.provider}, model: {config.model_name}")
    say("Real: Agent Runtime (WP1), Decision Engine (WP2), LLM, analyze_text handler.\n"
        "Stand-ins: marketplace, bidding, event log, registry (labelled below).")
    print(LINE)

    tasks = demo_tasks(time.time())
    registry = InMemoryAgentRegistry()
    capabilities = InMemoryCapabilityRegistry()
    capabilities.register("analyze_text", analyze_text)
    marketplace = DemoMarketplace(tasks)
    bidding = DemoBiddingStub()
    events = NarratingEventLog(tasks, factory)

    await registry.register(AgentDefinition(
        agent_id=AGENT_ID,
        name="Text Analyst Agent",
        capability_manifest=["analyze_text"],
    ))
    say(f"Registered agent {AGENT_ID} with capability manifest ['analyze_text']\n")

    runtime = AgentRuntime(
        registry,
        marketplace=marketplace,
        decision_engine_factory=factory,
        bidding_engine=bidding,
        capability_registry=capabilities,
        history_writer=events,
        execution_dispatcher=DemoDispatcher(),
    )
    agent = await runtime.load_agent(AGENT_ID)
    await agent.initialize()
    await agent.activate()

    # Two cycles: the first decides, the second shows the decision memory.
    waiter = asyncio.ensure_future(events.wait_for_cycles(2))
    done, _ = await asyncio.wait(
        {waiter, agent._decision_task},
        timeout=timeout_seconds,
        return_when=asyncio.FIRST_COMPLETED,
    )
    if waiter not in done:
        waiter.cancel()
        if agent.state == AgentState.ERROR:
            task = agent._decision_task
            error = task.exception() if task is not None and task.done() else None
            say(f"\nDEMO STOPPED: the agent entered ERROR ({type(error).__name__}: {error})")
            cause = getattr(error, "__cause__", None)
            if cause is not None:
                say(f"  caused by: {type(cause).__name__}: {cause}")
        else:
            say(f"\nDEMO STOPPED: no 2 completed cycles within {timeout_seconds:.0f}s "
                "(the LLM may be rate-limited; see messages above).")
            await agent.shutdown()
        return 1

    print(LINE)
    say("Lifecycle controls (WP1):")
    await agent.pause()
    await agent.resume()
    await agent.shutdown()

    reader_calls = factory.task_reader.model_calls if factory.task_reader else 0
    judge_calls = agent.decision_engine.model_calls
    print(LINE)
    say("SUMMARY")
    say(f"LLM requests: {reader_calls + judge_calls} "
        f"(shared reading: {reader_calls}, private judgement: {judge_calls})", indent=2)
    say(f"Decisions recorded in the agent's private history: "
        f"{len(agent.decision_engine.history.recent())}", indent=2)
    say(f"Handed to bidding: {', '.join(bidding.received) or 'none'} "
        "(once, even though the marketplace kept listing it)", indent=2)
    say(f"Final agent state: {agent.state.value}", indent=2)
    if offline:
        say("Reminder: this was an OFFLINE REHEARSAL with scripted answers.", indent=2)
    print(LINE)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--offline", action="store_true",
        help="rehearse with scripted answers; no LLM is called",
    )
    args = parser.parse_args()
    return asyncio.run(run(offline=args.offline))


if __name__ == "__main__":
    sys.exit(main())
