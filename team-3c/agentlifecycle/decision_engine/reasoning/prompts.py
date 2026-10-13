from langchain_core.prompts import ChatPromptTemplate
from .instructions import SYSTEM_INSTRUCTIONS

DECISION_PROMPT = ChatPromptTemplate.from_messages([
    ("system", SYSTEM_INSTRUCTIONS),
    ("human", '''
Agent context:
{agent_context}

Recent decision history:
{history}

Available marketplace tasks:
{tasks}

Market context:
{market_context}

Evaluate the available tasks. For each task, determine:
- capability compatibility;
- match score from 0.0 to 1.0;
- suitability;
- capability gaps;
- bid ratio when suitable;
- estimated completion time when supportable.

Follow the platform policy constraints supplied by the application.
Return a structured DecisionBatch.
'''),
])

NEGOTIATION_PROMPT = ChatPromptTemplate.from_messages([
    ("system", SYSTEM_INSTRUCTIONS),
    ("human", '''
Evaluate the following counter-offer for task {task_id}.

Current terms:
{current_terms}

Agent context:
{agent_context}

Recent history:
{history}

Determine whether to accept or reject the terms, or provide revised
terms when appropriate. Return a structured NegotiationDecision.
''"),
])
