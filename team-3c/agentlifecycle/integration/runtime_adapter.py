from decision_engine.context.agent_context import AgentDecisionContext
from decision_engine.engine import DecisionEngine

def build_decision_engine_from_runtime_agent(agent_definition) -> DecisionEngine:
    max_tasks = agent_definition.configuration.get("max_concurrent_tasks", 3)
    context = AgentDecisionContext(
        agent_id=agent_definition.agent_id,
        capabilities=agent_definition.capabilities,
        performance=agent_definition.performance,
        max_concurrent_tasks=max_tasks,
        configuration=agent_definition.configuration,
    )
    return DecisionEngine(agent_context=context)
