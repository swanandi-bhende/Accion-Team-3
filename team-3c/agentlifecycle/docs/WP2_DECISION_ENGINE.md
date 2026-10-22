# WP2 — Decision Engine (Team 3C)

Owner: Ishan Patankar (WP2, project lead). Branch: `wp2-decision-engine`.

The Decision Engine answers one question for each agent: **"is this task
suitable for this agent?"** It never sets a price, places a bid, runs a
workload or negotiates — those belong to WP3 (bidding) and Team 3B
(execution).

---

## 1. How a decision is made

Each agent has its **own** Decision Engine (private context and history),
built by `DecisionEngineFactory`. All agents **share** one LLM connection
(one API key), the prompts, the default rules and one **Task Reader**.

For every decision cycle, cheapest checks first and LLM calls last:

| Step | What happens | LLM? |
|---|---|---|
| 0 | Tasks this agent already decided for good (and unchanged) are skipped | no |
| 1 | Agent full (no free task slots) → every task `capacity_full` | no |
| 2 | Task not open → `task_not_open`; deadline passed → `deadline_passed` | no |
| 3 | **Shared Task Reader**: reads each task description **once** (cached, shared by all agents) and returns capability IDs from the **capability catalogue**; anything outside it is "unsupported". Skipped if the task declares its capabilities, or if the agent has none. | shared, once per task |
| 4 | **Capability check in code**: every needed capability must be in the agent's verified `capability_manifest`; unsupported needs can never be met → `capability_missing` | no |
| 5 | **Private judgement**: this agent's LLM call, only for tasks that passed | per agent |
| 6 | Hard rules on the answer: unknown task IDs ignored + logged; missing / duplicate answer rejected; must be `suitable` **and** score ≥ 0.7 | no |
| 7 | Free slots filled best-score-first; the rest `capacity_full` | no |
| 8 | Only new or changed decisions are returned and recorded | no |

**Why both an LLM and rules?** Team 3A's on-chain task has no capability
field — only a description hash — so reading the description needs
language understanding (the LLM). Rules cannot read text, but they make the
outcome safe and predictable: a rule can veto, it can never approve on its
own. *"The LLM does the judgement on unstructured task descriptions;
deterministic rules guarantee safety and predictability."*

### Rejection reasons

| Reason | Meaning | Final? |
|---|---|---|
| `capability_missing` | agent lacks a needed capability (gap listed) | final |
| `requirements_unspecified` | nothing to check: no declared capabilities and nothing identifiable in the description | final |
| `model_not_suitable` | the LLM said no | final |
| `score_below_threshold` | match score < 0.7 | final |
| `deadline_passed` | deadline (Unix seconds, as in Team 3A) passed | final |
| `capacity_full` | no free task slot right now | temporary |
| `requirements_unreadable` | reader gave no / duplicate answer | temporary |
| `missing_model_decision` / `duplicate_task_decision` | private answer missing / duplicated | temporary |
| `task_not_open` | task status is not open | temporary |

**Final** outcomes (and approvals) are not re-judged while the task's
description and declared capabilities stay the same. **Temporary** ones are
checked again next cycle. A changed description = a new judgement.

---

## 2. Contracts with other work packages

**WP1 (runtime)**
- `AgentRuntime(..., decision_engine_factory=...)` — the factory builds one
  engine per loaded agent (`interfaces/runtime_services.DecisionEngineFactoryService`).
- Engine readiness: `engine.ready` is True only when a model is configured.
- Errors: every `DecisionEngineError` has `retryable`. The runtime skips a
  cycle and retries with backoff (2, 4, 8, 16, 30 s) on `retryable=True`
  (rate limit, timeout, network, provider server error); anything else
  moves the agent to ERROR as before.

**WP3 (bidding / marketplace)**
- Input to bidding: `DecisionResult` with `suitable=True`. Fields:
  `task_id, suitable, match_score, model_suitable, rejection_reasons,
  capability_gap, required_capabilities, requirements_source,
  estimated_completion_seconds, reasoning`. **No price fields.**
- Each approved task is handed over **once** (the engine remembers it).
- Bid bounds from Requirement 2.3 (50–95 % of bounty) are documented in
  `policy/constraints.py` for WP3; the Decision Engine never uses them.
- Negotiation / counter-offers: WP3 (removed from the Decision Engine).
- Tasks may come with or without `required_capabilities`; without them the
  Task Reader reads the description.

**WP4 (events / history)**
- `active_task_counter` hook (async `agent_id -> int`) on the factory: the
  real number of active (awarded) tasks should come from WP4's records.
  Until then the count is 0 (true today: nothing can be awarded yet).
- The engine's memory and history are in-memory; persistence is WP4's.

**Capability catalogue** — `task_reading/catalogue.py`. The shared list of
capability IDs (with descriptions) the reader may answer with. The demo
catalogue holds `analyze_text` (WP1's real handler). Every capability that
gets a real handler should be added here.

---

## 3. Setup and commands (Windows PowerShell)

From the `agentlifecycle` folder:

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements-dev.txt
.venv\Scripts\python -m pytest tests -q          # unit + integration tests, offline
```

LLM settings (set in the PowerShell window; never commit keys):

```powershell
$env:DECISION_LLM_PROVIDER = "gemini"
$env:GOOGLE_API_KEY = "<your key from aistudio.google.com>"
```

| Variable | Default | Notes |
|---|---|---|
| `DECISION_LLM_PROVIDER` | `openai` | `gemini` or `openai` |
| `GOOGLE_API_KEY` (or `GEMINI_API_KEY`) | — | Gemini key |
| `OPENAI_API_KEY` | — | OpenAI key |
| `DECISION_LLM_MODEL` | gemini: `gemini-3.8-flash`, openai: `gpt-4o-mini` | empty = default |
| `DECISION_LLM_TEMPERATURE` | gemini: provider default, openai: `0` | Google recommends the default (1.0) for Gemini 3 |
| `DECISION_LLM_TIMEOUT_SECONDS` | `30` | per request |
| `DECISION_LLM_MAX_RETRIES` | `1` | provider-level retries |

Real checks (use the key):

```powershell
.venv\Scripts\python smoke_test_llm.py              # 2 requests: proves both LLM steps work
.venv\Scripts\python demo_decision_engine.py        # live demo, ~2 requests
.venv\Scripts\python demo_decision_engine.py --offline   # rehearsal, scripted, no LLM
```

**Data note:** Google's pricing page marks free-tier Gemini usage as "used to
improve our products". Send simulated tasks only — never real client data.

---

## 4. Decision log

| Date | Decision (by lead) | Why |
|---|---|---|
| 2026-10-19 | Fix `prompts.py` syntax error; provider factory + env config; key hidden from `repr` | Decision Engine could not be imported; secrets must never be logged (Rule 10) |
| 2026-10-19 | One Decision Engine per agent via a factory; shared model/prompts/policy, private context/history | Runtime gave one engine to all agents; matches Team 3C design (`decision_engine_factory.create(agent)`) |
| 2026-10-19 | `capability_manifest` is the only authoritative capability list | Only the manifest is verified against real handlers (Rule 6) |
| 2026-10-19 | Remove bid calculation from the Decision Engine | Manual Section 8: suitability vs price are separate components |
| 2026-10-19 | Hard rules after the LLM; typed errors; 60 s per-call budget | LLM output is a proposal; a hung call must not block pause/shutdown |
| 2026-10-19 | History starts empty; no `performance` dict or raw config in prompts | Rule 1: no fabricated history; Rule 10: no secrets to the provider |
| 2026-10-19 | Support Gemini (free tier for demo) and OpenAI; Gemini uses its default temperature | No funded API yet; Google advises against temperature < 1.0 on Gemini 3 |
| 2026-10-20 | Two-step flow: shared Task Reader + code capability check + private judgement | Team 3A tasks have no capability field (README + design doc); one shared reading is consistent and cheaper |
| 2026-10-21 | Agent remembers decisions; final vs temporary outcomes; changed description = re-judge | A live loop re-asked the LLM every cycle and re-sent tasks to bidding |
| 2026-10-21 | Retryable errors; runtime retries with backoff instead of ERROR (WP1 change, approved) | A free-tier rate limit must not kill an agent; non-retryable behaviour unchanged |
| 2026-10-21 | Remove negotiation and unused scaffold from the Decision Engine | Everything bidding-related is WP3 |
| 2026-10-21 | Demo uses the real runtime with labelled stand-ins | Show the real decision flow honestly; stand-ins are not WP3/WP4 work |

---

## 5. Known limits and open questions

- **Real Gemini run:** the smoke test and demo must be run with a real key
  (not possible from the development sandbox).
- **Restart:** decision memory and history are in-memory until WP4's event
  store exists; after a restart, tasks are judged again.
- **Concurrency across cycles:** the active-task count counts awarded tasks
  only. Limiting how many *pending bids* an agent holds (Requirement 4.6) is
  WP3's Bid Manager.
- **Polling interval:** the runtime waits 2 s between cycles; Requirement 1.3
  asks for a configurable interval (default 10 s) — WP1.
- **Catalogue ownership:** who maintains the capability catalogue once real
  agents add capabilities.
- **For Team 3A:** where is the task description text stored (only
  `descriptionHash` is on-chain)? There is no bid contract — where do bids
  go? What records the winner? Bid against `bountyAmount` or escrowed amount?
- **For Accion Labs:** which LLM/API account to use, and whether data may be
  sent to a free-tier API.
