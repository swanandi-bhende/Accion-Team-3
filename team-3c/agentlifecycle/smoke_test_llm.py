"""Real-provider smoke test for the Decision Engine (manual Task 3.1, item 73).

Makes real LLM requests (2 for this demo), so it needs a real API key in the
environment. It is deliberately not named test_*.py, so `pytest tests`
never runs it and unit tests stay free and offline.

Run from the agentlifecycle folder, for example (PowerShell):

    $env:DECISION_LLM_PROVIDER = "gemini"
    $env:GOOGLE_API_KEY = "<your key>"
    .venv\\Scripts\\python smoke_test_llm.py

The two simulated tasks carry ONLY a description, like real Team 3A tasks
(on-chain there is no capability field), so the run shows both steps:

  Step 1  shared Task Reader: description -> catalogue capability IDs
  code    does the agent have every capability?
  Step 2  the agent's private LLM judgement, only for tasks that passed

Only simulated demo tasks are sent. Gemini free-tier prompts may be used by
Google to improve its products, so never put real client data in here.

Connection/format problems FAIL the test. The model's judgement calls (what
it read, whether it likes the task) are shown and only WARN if surprising.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time

from agents.models import AgentDefinition
from decision_engine.errors import DecisionEngineError
from decision_engine.factory import DecisionEngineFactory
from decision_engine.model.configuration import (
    DecisionModelConfiguration,
    ProviderConfigurationError,
)
from decision_engine.model.provider import ProviderDependencyError
from decision_engine.models.task import MarketplaceTask
from decision_engine.task_reading.catalogue import DEMO_CAPABILITY_CATALOGUE

SIMULATION = {"environment": "simulation", "source": "smoke_test_llm.py"}


def _demo_tasks(now: float) -> list[MarketplaceTask]:
    # No required_capabilities on purpose: the reader must work them out.
    return [
        MarketplaceTask(
            task_id="SIM-TEXT-001",
            description=(
                "Count the words and characters in a short supplied paragraph "
                "and return the statistics as JSON."
            ),
            bounty_amount=100.0,
            deadline=int(now) + 3600,
            market_context=SIMULATION,
        ),
        MarketplaceTask(
            task_id="SIM-IMAGE-002",
            description="Generate a 1024x1024 photorealistic product image of a red shoe.",
            bounty_amount=250.0,
            deadline=int(now) + 3600,
            market_context=SIMULATION,
        ),
    ]


async def main() -> int:
    try:
        config = DecisionModelConfiguration.from_env()
        factory = DecisionEngineFactory.from_configuration(
            config, capability_catalogue=DEMO_CAPABILITY_CATALOGUE
        )
    except (ProviderConfigurationError, ProviderDependencyError) as exc:
        print(f"SETUP FAILED: {exc}")
        return 1

    print(f"Provider: {config.provider} | model: {config.model_name}")
    print(
        "Temperature: "
        f"{config.temperature if config.temperature is not None else 'provider default'}"
    )

    agent = AgentDefinition(
        agent_id="smoke-test-agent",
        name="Smoke Test Agent",
        capability_manifest=["analyze_text"],
    )
    engine = factory.create(agent)

    started = time.monotonic()
    try:
        results = await engine.evaluate_tasks(_demo_tasks(time.time()))
    except DecisionEngineError as exc:
        print(f"SMOKE TEST FAILED ({type(exc).__name__}): {exc}")
        if exc.__cause__ is not None:
            print(f"  caused by: {type(exc.__cause__).__name__}: {exc.__cause__}")
        return 1
    elapsed = time.monotonic() - started

    print(f"Finished in {elapsed:.1f}s "
          f"(shared reading calls: {factory.task_reader.model_calls})\n")
    for result in results:
        print(json.dumps(result.model_dump(), indent=2))

    by_id = {r.task_id: r for r in results}
    text, image = by_id.get("SIM-TEXT-001"), by_id.get("SIM-IMAGE-002")

    failures, warnings = [], []
    if text is None or image is None:
        failures.append("a task is missing from the results")
    else:
        for r in (text, image):
            if r.requirements_source not in ("extracted", "unspecified"):
                failures.append(
                    f"{r.task_id}: Step 1 reading did not complete "
                    f"(source={r.requirements_source})"
                )
        if text.requirements_source == "extracted" and text.model_suitable is None:
            if text.rejection_reasons != ["capability_missing"]:
                failures.append("SIM-TEXT-001: Step 2 returned no decision")
        if text.model_suitable is not None and not 0.0 <= text.match_score <= 1.0:
            failures.append("SIM-TEXT-001: match_score out of range")

        if text.required_capabilities != ["analyze_text"]:
            warnings.append(
                f"SIM-TEXT-001 was read as {text.required_capabilities}, "
                "expected ['analyze_text']"
            )
        if image.rejection_reasons != ["capability_missing"]:
            warnings.append(
                "SIM-IMAGE-002 was expected to be rejected as capability_missing, "
                f"got {image.rejection_reasons}"
            )
        if image.model_suitable is not None:
            warnings.append("SIM-IMAGE-002 reached the private judgement step")

    for warning in warnings:
        print(f"\nWARNING (model judgement, not a connection problem): {warning}")
    if failures:
        print("\nSMOKE TEST FAILED: " + "; ".join(failures))
        return 1

    print(
        "\nSMOKE TEST PASSED: the real provider answered both steps with valid "
        "structured output, and the code checks were applied."
    )
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
