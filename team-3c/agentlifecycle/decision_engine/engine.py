import json
from langchain_core.language_models.chat_models import BaseChatModel
from .models.decision import DecisionBatch, DecisionRecord, DecisionResult
from .models.task import MarketplaceTask
from .models.negotiation import NegotiationTerms, NegotiationDecision
from .context.agent_context import AgentDecisionContext
from .memory.history import DecisionHistory
from .policy.decision_policy import DecisionPolicy
from .reasoning.prompts import DECISION_PROMPT, NEGOTIATION_PROMPT

class DecisionEngineModelNotConfigured(RuntimeError):
    pass

class DecisionEngine:
    CONTEXT_WINDOW_SIZE = 20

    def __init__(self, agent_context: AgentDecisionContext,
                 model: BaseChatModel | None = None,
                 history: DecisionHistory | None = None,
                 policy: DecisionPolicy | None = None):
        self.agent_context = agent_context
        self.model = model
        self.history = history or DecisionHistory()
        self.policy = policy or DecisionPolicy()
        self.chain = None
        if model is not None:
            self._build_chain(model)

    @property
    def ready(self) -> bool:
        return self.chain is not None

    def _build_chain(self, model: BaseChatModel):
        self.chain = DECISION_PROMPT | model.with_structured_output(DecisionBatch)

    def attach_model(self, model: BaseChatModel):
        self.model = model
        self._build_chain(model)

    async def evaluate_tasks(self, tasks: list[MarketplaceTask]) -> list[DecisionResult]:
        if not self.ready:
            raise DecisionEngineModelNotConfigured(
                "No LangChain chat model is configured."
            )

        response = await self.chain.ainvoke({
            "agent_context": self.agent_context.model_dump_json(),
            "history": json.dumps([r.model_dump() for r in self.history.recent()]),
            "tasks": json.dumps([t.model_dump() for t in tasks]),
            "market_context": json.dumps({t.task_id: t.market_context for t in tasks}),
        })

        known = {t.task_id: t for t in tasks}
        results = []

        for decision in response.decisions:
            task = known.get(decision.task_id)
            if task is None:
                continue

            suitable, ratio = self.policy.validate(
                decision.match_score, decision.bid_ratio,
                self.agent_context.active_task_count,
                self.agent_context.max_concurrent_tasks,
            )
            bid = self.policy.calculate_bid(task.bounty_amount, ratio) if suitable else None

            results.append(DecisionResult(
                task_id=task.task_id,
                match_score=decision.match_score,
                suitable=suitable,
                recommended_bid=bid,
                estimated_completion_seconds=decision.estimated_completion_seconds,
                capability_gap=decision.capability_gap,
                reasoning=decision.reasoning,
            ))

            self.history.add(DecisionRecord(
                task_id=task.task_id,
                match_score=decision.match_score,
                suitable=suitable,
                recommended_bid=bid,
            ))

        return sorted(results, key=lambda r: r.match_score, reverse=True)

    async def evaluate_negotiation(self, terms: NegotiationTerms) -> NegotiationDecision:
        if self.model is None:
            raise DecisionEngineModelNotConfigured(
                "No LangChain chat model is configured."
            )

        chain = NEGOTIATION_PROMPT | self.model.with_structured_output(
            NegotiationDecision
        )
        return await chain.ainvoke({
            "task_id": terms.task_id,
            "current_terms": terms.model_dump_json(),
            "agent_context": self.agent_context.model_dump_json(),
            "history": json.dumps([r.model_dump() for r in self.history.recent()]),
        })
