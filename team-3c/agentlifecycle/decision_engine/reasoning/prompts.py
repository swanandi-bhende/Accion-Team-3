from langchain_core.prompts import ChatPromptTemplate
from .instructions import SYSTEM_INSTRUCTIONS

# Bump whenever DECISION_PROMPT or SYSTEM_INSTRUCTIONS change in a way that
# could change decisions. Stored with every recorded decision (item 89).
DECISION_PROMPT_VERSION = "decision-v2"

# Sent instead of an empty list so the model cannot mistake "no data" for
# a record of anything (manual item 86).
NO_HISTORY_MARKER = "No decision history available. Do not assume any past performance."

DECISION_PROMPT = ChatPromptTemplate.from_messages([
    ("system", SYSTEM_INSTRUCTIONS),
    ("human", '''
Agent context:
{agent_context}

Recent decision history (this agent's own recorded decisions only):
{history}

Available marketplace tasks:
{tasks}

Market context:
{market_context}

Evaluate every available task, and only those tasks, using its exact task_id.
For each task, determine:
- capability compatibility with the agent's listed capabilities;
- match score from 0.0 to 1.0;
- suitability;
- capability gaps;
- estimated completion time in seconds, or null if the facts given do not
  support an estimate;
- a short reasoning for the decision.

Use only the information above. The application applies its own policy
checks after your answer. Return a structured DecisionBatch.
'''),
])
