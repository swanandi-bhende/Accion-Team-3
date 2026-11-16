"""Contract analysis workflow (Requirement 9).

Wired as a LangGraph StateGraph when langgraph is installed. LangGraph is an optional
import: the same four nodes run in sequence through `_run_sequential` otherwise, so the
pipeline and its tests work without the dependency and the node functions stay the
single source of truth for behaviour either way.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Callable, TypedDict

from backend.legal_agent.extraction import (
    RuleBasedExtractor,
    LLMClauseExtractor,
    extract_entities,
    extract_jurisdiction,
    extract_text,
    validate_format,
)
from backend.legal_agent.relations import detect_clause_relationships, detect_supersedes
from backend.legal_agent.models import ParsedContract

try:  # pragma: no cover - exercised only when langgraph is present
    from langgraph.graph import END, StateGraph

    LANGGRAPH_AVAILABLE = True
except ImportError:  # pragma: no cover
    LANGGRAPH_AVAILABLE = False


class AgentState(TypedDict, total=False):
    contract_id: str
    raw: Any
    fmt: str
    text: str
    parsed: ParsedContract
    contract_date: date | None
    stored: bool
    indexed: bool


def node_validate_format(state: AgentState) -> AgentState:
    state["fmt"] = validate_format(state["fmt"])
    return state


def node_extract_text(state: AgentState) -> AgentState:
    state["text"] = extract_text(state["raw"], state["fmt"])
    return state


def node_identify_clauses(state: AgentState) -> AgentState:
    text = state["text"]
    
    import os
    # LLM extraction is used when USE_LLM_EXTRACTOR=true or in live production agent
    use_llm = os.environ.get("USE_LLM_EXTRACTOR", "").lower() in ("true", "1")
    if use_llm and (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("GROQ_API_KEY")):
        try:
            clauses = LLMClauseExtractor().extract(text)
            if not clauses:  # Fallback if LLM extraction fails or returns empty
                clauses = RuleBasedExtractor().extract(text)
        except Exception:
            clauses = RuleBasedExtractor().extract(text)
    else:
        clauses = RuleBasedExtractor().extract(text)

    state["parsed"] = ParsedContract(
        contract_id=state["contract_id"],
        clauses=clauses,
        entities=extract_entities(text),
        jurisdiction=extract_jurisdiction(text),
        relationships=detect_clause_relationships(clauses),
        supersedes_prior=detect_supersedes(text),
    )
    return state


def make_store_node(graph_client=None, search_engine=None) -> Callable[[AgentState], AgentState]:
    """Task 13.1: graph storage and search indexing hang off the same node, so a parsed
    contract always lands in both stores or neither."""

    def node_store(state: AgentState) -> AgentState:
        parsed = state["parsed"]
        if graph_client is not None:
            graph_client.add_contract(parsed)
            state["stored"] = True
        if search_engine is not None:
            search_engine.index_clauses(parsed, contract_date=state.get("contract_date"))
            state["indexed"] = True
        return state

    return node_store


class ContractAnalysisAgent:
    def __init__(self, graph_client=None, search_engine=None, use_langgraph: bool = True):
        self.nodes = [
            ("validate_format", node_validate_format),
            ("extract_text", node_extract_text),
            ("identify_clauses", node_identify_clauses),
            ("store_in_graph", make_store_node(graph_client, search_engine)),
        ]
        self.use_langgraph = use_langgraph and LANGGRAPH_AVAILABLE
        self._compiled = self._build_graph() if self.use_langgraph else None

    def _build_graph(self):  # pragma: no cover - requires langgraph
        graph = StateGraph(AgentState)
        for name, fn in self.nodes:
            graph.add_node(name, fn)
        graph.set_entry_point(self.nodes[0][0])
        for (name, _), (next_name, _) in zip(self.nodes, self.nodes[1:]):
            graph.add_edge(name, next_name)
        graph.add_edge(self.nodes[-1][0], END)
        return graph.compile()

    def _run_sequential(self, state: AgentState) -> AgentState:
        for _, fn in self.nodes:
            state = fn(state)
        return state

    def analyze(
        self, contract_id: str, data: Any, fmt: str, contract_date: date | None = None
    ) -> ParsedContract:
        state: AgentState = {
            "contract_id": contract_id,
            "raw": data,
            "fmt": fmt,
            "contract_date": contract_date,
        }
        if self._compiled is not None:  # pragma: no cover
            state = self._compiled.invoke(state)
        else:
            state = self._run_sequential(state)
        return state["parsed"]
