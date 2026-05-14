# Multi-Agent Analysis

Visual guide to how `aipa analyze` and `aipa deep-research` use AI agents to produce market analysis.

---

## Two Commands, Two Architectures

```
  aipa analyze VCB                  aipa deep-research "banking sector"
  ┌──────────────────┐              ┌──────────────────────────────────────┐
  │   Single Agent   │              │        Multi-Agent Pipeline          │
  │                  │              │                                      │
  │  Build context   │              │  Supervisor -> Workers -> Aggregator │
  │  Send to LLM     │              │       -> Reviewer (up to 3 rounds)   │
  │  Stream response │              │                                      │
  │                  │              │  Parallel sector analysis            │
  │  1 LLM call      │              │  4-6 LLM calls                       │
  └──────────────────┘              └──────────────────────────────────────┘
  Fast (~10-30s)                    Slow (~5-10 min) but thorough
```

---

## Shared: Agent Tools

Both commands use the same set of tools. The AI decides when to call them.

```
  Available tools:

  ┌───────────────────┬─────────────────────────────────────────────────┐
  │ get_live_data     │ Latest candle for tickers (top 50 by value)     │
  │ get_ohlcv_data    │ Historical OHLCV with MA indicators             │
  │ get_ticker_list   │ List tickers with metadata (sector, source)     │
  │ get_performers    │ Top/worst performers ranked by metric           │
  │ get_volume_profile│ Volume-by-price histogram with POC & VA         │
  └───────────────────┴─────────────────────────────────────────────────┘
```

The agent doesn't call every tool every time. It picks based on the question:

```
  User: "How is VCB today?"          -> get_live_data("VCB")
  User: "Compare VCB and BID"        -> get_ohlcv_data(["VCB","BID"], "1D", 50)
  User: "Which bank is strongest?"   -> get_performers(sort_by="ma50_score", group="Banking")
  User: "Support/resistance VCB?"    -> get_volume_profile("VCB")
```

---

## `aipa analyze` — Single Agent

### Flow

```
  User runs: aipa analyze VCB --question "Should I buy?"

  ┌─────────────────────────────────────────────────────────┐
  │ Step 1: Build context                                   │
  │                                                         │
  │   AIContextBuilder gathers:                             │
  │   - OHLCV data (1D x 20 bars) for VCB                   │
  │   - MA indicators (ma10..ma200, scores)                 │
  │   - Live data overlay                                   │
  │   - System prompt (persona + analysis framework)        │
  │   - Reference ticker context (if auto-detected)         │
  │   -> ~5,000-15,000 chars of pre-assembled context       │
  └──────────────────────┬──────────────────────────────────┘
                         v
  ┌─────────────────────────────────────────────────────────┐
  │ Step 2: Send to LLM as single message                   │
  │                                                         │
  │   <analysis_context>                                    │
  │     ... all the data above ...                          │
  │   </analysis_context>                                   │
  │                                                         │
  │   Should I buy VCB?                                     │
  │                                                         │
  │   You have tools available. Use them if you need        │
  │   additional data beyond what is provided above.        │
  └──────────────────────┬──────────────────────────────────┘
                         v
  ┌─────────────────────────────────────────────────────────┐
  │ Step 3: Stream response                                 │
  │                                                         │
  │   Agent may:                                            │
  │   - Answer directly from the context (no tool call)     │
  │   - Call get_live_data() to check today's price         │
  │   - Call get_ohlcv_data() for more history              │
  │   - Call get_volume_profile() for support/resistance    │
  │                                                         │
  │   Events streamed to terminal:                          │
  │   [thinking] ... reasoning tokens ...                   │
  │   [tool] get_live_data(tickers="VCB")                   │
  │   [tool-result] VCB close=80,900 vol=12.5M              │
  │   [result] ... final analysis text ...                  │
  └─────────────────────────────────────────────────────────┘
```

### Key features

- **Context pre-built**: data is gathered *before* the LLM call, not on-demand
- **Tool-augmented**: the agent can fetch *more* data if the pre-built context isn't enough
- **Streaming**: thinking, tool calls, and tokens arrive in real-time
- **Multi-ticker**: pass multiple tickers for comparison (`aipa analyze VCB FPT MWG`)

### CLI flags

```
  aipa analyze VCB                          # default question
  aipa analyze VCB --question "buy or sell?" # custom question
  aipa analyze VCB FPT --interval 1h        # hourly data
  aipa analyze VCB --context-only            # dump context, no LLM
  aipa analyze VCB --questions               # list available templates
  aipa analyze VCB --lang vn                 # Vietnamese response
  aipa analyze VCB --verbose                 # show thinking tokens
```

---

## Three Ways to Get Analysis

The user chooses who does the thinking. This applies to both `analyze` and `deep-research`.

| | Option A: Built-in agent | Option B: Skills (no API key) | Option C: Skills + API key |
|---|---|---|---|
| Who analyzes | Built-in LLM | Your AI agent (Claude Code, Gemini CLI, Codex) | Built-in LLM, then your AI agent on top |
| How | `aipa analyze VCB` | AI agent calls `aipa analyze VCB --context-only` | AI agent calls `aipa analyze VCB` |
| What the AI agent sees | N/A (direct to terminal) | Raw context (OHLCV, MA scores, etc.) | Built-in analysis — then adds its own layer |
| `deep-research` | `aipa deep-research --run` (full pipeline) | AI agent calls `aipa deep-research` (snapshot) | AI agent calls `aipa deep-research --run` |
| Requires | `aipa setup` (OPENAI_API_KEY) | Nothing extra | `aipa setup` (OPENAI_API_KEY) |
| LLM cost | Your API tokens | Your AI agent's subscription | Your API tokens + your AI agent's subscription |

```
  Option A: direct terminal usage
  ─────────────────────────────────
  Terminal -> aipa analyze VCB -> built-in LLM -> analysis printed to terminal

  Option B: AI agent does the thinking (no API key)
  ─────────────────────────────────
  Claude Code -> aipa analyze VCB --context-only -> raw context returned
             -> Claude reads context and analyzes with its own LLM

  Option C: double agent (built-in + your AI agent)
  ─────────────────────────────────
  Claude Code -> aipa analyze VCB -> built-in LLM -> analysis returned
             -> Claude reads the analysis and adds its own insights on top
             -> two layers of AI analysis, but costs API tokens
```

The `skills/` directory (aipa-data, aipa-analyze, aipa-research) provides Options B and C out of the box.

---

## `aipa deep-research` — Multi-Agent Pipeline

### Architecture

```
  User runs: aipa deep-research "Analyze banking sector" --run

  NOTE: Without --run, only the market snapshot is printed (no LLM calls, fast).
        The --run flag is required to trigger the full multi-agent pipeline.

  ┌─────────────────────────────────────────────────────┐
  │  Market Snapshot                                    │
  │  Fetch top tickers, latest daily bar, MA scores     │
  │  (~500 tickers)                                     │
  └──────────────────────┬──────────────────────────────┘
                         v
  ┌─────────────────────────────────────────────────────────────┐
  │                    SUPERVISOR AGENT                         │
  │                                                             │
  │  Input: market snapshot + user question                     │
  │  Tool:  create_subtasks                                     │
  │  Output: 3-5 sector subtasks with top 10 tickers each       │
  │                                                             │
  │  "Banking: VCB, BID, TCB, MBB, CTG, VPB, TPB, STB, HDB, VCB"│
  │  "Real Estate: VHM, VIC, NVL, KBC, SZC, PDR, TCH, HQC, ..." │
  │  "Steel: HPG, HSG, NKG, TLH, ..."                           │
  └──────────────────────┬──────────────────────────────────────┘
                         │ fan_out (parallel)
          ┌──────────────┼──────────────┐
          v              v              v
  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐
  │  WORKER 1    │ │  WORKER 2    │ │  WORKER 3    │
  │  Banking     │ │  Real Estate │ │  Steel       │
  │              │ │              │ │              │
  │ Input:       │ │ Input:       │ │ Input:       │
  │ snapshot +   │ │ snapshot +   │ │ snapshot +   │
  │ instruction  │ │ instruction  │ │ instruction  │
  │ + tickers    │ │ + tickers    │ │ + tickers    │
  │              │ │              │ │              │
  │ Tools:       │ │ Tools:       │ │ Tools:       │
  │ get_ohlcv    │ │ get_ohlcv    │ │ get_ohlcv    │
  │ get_live     │ │ get_live     │ │ get_live     │
  │ get_tickers  │ │ get_tickers  │ │ get_tickers  │
  │              │ │              │ │              │
  │ -> sector    │ │ -> sector    │ │ -> sector    │
  │    report    │ │    report    │ │    report    │
  └──────┬───────┘ └──────┬───────┘ └──────┬───────┘
         └────────────────┼────────────────┘
                          v
  ┌────────────────────────────────────────────────────────────┐
  │                    AGGREGATOR AGENT                        │
  │                                                            │
  │  Input: worker sector reports + user question              │
  │  (no market snapshot — synthesizes text only)              │
  │  Tools: none (no market data access)                       │
  │  Job:   synthesize into unified analysis                   │
  │                                                            │
  │  - Cross-reference findings across sectors                 │
  │  - Build multi-sector ranking table                        │
  │  - Identify rotation patterns                              │
  │  - Unified recommendation                                  │
  │                                                            │
  │  -> Draft report                                           │
  └──────────────────────┬─────────────────────────────────────┘
                         v
  ┌────────────────────────────────────────────────────────────┐
  │                    REVIEWER AGENT                          │
  │                                                            │
  │  Input: worker sector reports + aggregator's draft report  │
  │  (no market snapshot — reviews text only)                  │
  │  Tools: approve_report, reject_report                      │
  │  Checks:                                                   │
  │  - No phantom stocks (tickers that don't exist)            │
  │  - MA score fidelity (scores match actual data)            │
  │  - Table completeness (no missing columns)                 │
  │  - No fabricated numbers                                   │
  │                                                            │
  │  APPROVE -> done, ship report                              │
  │  REJECT  -> send feedback back to Aggregator (retry)       │
  └──────────────────────┬─────────────────────────────────────┘
                         │
                    ┌────┴────┐
                    v         v
                 approve    reject
                    │         │
                    v         v
               ┌─────────┐  back to Aggregator
               │  FINAL  │  (max 3 rounds)
               │  REPORT │
               └─────────┘
```

### Why multiple agents?

```
  Single agent:                           Multi-agent pipeline:

  One prompt with everything.             Each agent has a focused job.
  500 tickers in one context window.      Supervisor: decompose only.
  LLM gets overwhelmed.                   Workers: research one sector each.
  Shallow analysis.                       Aggregator: synthesize only.
                                          Reviewer: quality gate.

  Misses cross-sector patterns.          Parallel workers = deeper per-sector.
  Can't verify its own output.           Reviewer catches hallucinations.
```

### The review loop

The reviewer can reject the aggregator's draft and send it back for fixes:

```
  Round 1:  Aggregator -> "Banking sector has VCB at +8.5%"
            Reviewer   -> REJECT "VCB's actual MA50 score is +3.2%, not +8.5%"
                         "Also missing NKG in the steel sector table"

  Round 2:  Aggregator -> fixes numbers, adds NKG
            Reviewer   -> APPROVE

  (max 3 rounds; if reviewer never approves, last draft is used)
```

### CLI flags

```
  aipa deep-research                       # snapshot only (no LLM, fast)
  aipa deep-research --run                 # full pipeline (5-10 min)
  aipa deep-research "banking sector" --run # custom question
  aipa deep-research --source crypto --run # crypto market
  aipa deep-research --lang vn --run       # Vietnamese report
  aipa deep-research --resume ID           # resume interrupted run
```

---

## Comparison

```
                        analyze              deep-research
                        ───────              ──────────────
  Speed                 ~10-30s              ~5-10 min
  LLM calls             1                    4-6+
  Agents                1                    4 roles
  Parallel workers      no                   yes (fan-out)
  Review/quality gate   no                   yes (up to 3 rounds)
  Best for              single ticker,       multi-sector,
                        quick question       comprehensive report
  Context               pre-built            agents fetch on-demand
  Resume interrupted    no                   yes (--resume)
```

---

## Under the Hood

### Only workers are real agents

Despite the name "multi-agent", only the workers run an agent loop. The other nodes are plain LLM calls:

| Node | How it calls the LLM | Tools? |
|---|---|---|
| Supervisor | `llm.bind_tools([create_subtasks]).ainvoke()` | One-shot tool call |
| Workers | `_run_agent_with_tools()` (full agent loop) | Iterative tool calls |
| Aggregator | `llm.ainvoke()` | None |
| Reviewer | `llm.bind_tools([approve/reject]).ainvoke()` | One-shot tool call |

```
  Workers (agent loop):                Everyone else (one-shot):

  ┌─────────────────────┐              ┌──────────────────┐
  │  LLM -> tool call?  │              │                  │
  │    yes -> execute    │              │  input ─> output │
  │    feed result back  │              │                  │
  │  LLM -> tool call?  │              │  One call. Done. │
  │    yes -> execute    │              └──────────────────┘
  │  ...repeat...        │
  │  LLM -> final text   │
  └─────────────────────┘
```

### Fake tools for structured output

`create_subtasks`, `approve_report`, and `reject_report` are **schema definitions disguised as functions**. Their return values are never used — what matters is the parameter schema and docstring:

```python
@tool
def create_subtasks(subtasks: list[dict]) -> str:
    """Create research subtasks... Each subtask: sector, tickers, instruction."""
    return json.dumps({"subtasks": subtasks})   # <- never actually called
```

LangChain converts this into a JSON schema that the LLM sees. The LLM generates a structured tool call with arguments matching that schema. The code then reads the arguments directly:

```
  1. llm.bind_tools([create_subtasks])     <- LLM sees the schema
  2. LLM generates: {"subtasks": [...]}    <- structured output
  3. Code reads: response.tool_calls[0]["args"]["subtasks"]  <- what we use
  4. Function body never executes
```

This is a common LangChain pattern — use tool calling to get structured JSON output from the LLM instead of parsing freeform text.

### How supervisor fans out to parallel workers

```
  ┌──────────────────────────────────────────────────────────┐
  │  graph.add_edge(START, "supervisor")                     │
  │  graph.add_conditional_edges("supervisor", fan_out, ...) │
  │  graph.add_edge("worker", "aggregator")                  │
  │  graph.add_edge("aggregator", "reviewer")                │
  │  graph.add_conditional_edges("reviewer", review_router)  │
  │  graph.add_edge("end", END)                              │
  └──────────────────────────────────────────────────────────┘
```

The `fan_out` function is not a node — it's a **router**. It reads `state["subtasks"]` (set by supervisor) and returns N `Send("worker", {...})` objects. LangGraph runs them all in parallel:

```
  Supervisor returns:  {"subtasks": [Banking, Steel, Real Estate]}

  fan_out reads state["subtasks"] and creates:
    Send("worker", {sector: "Banking",     tickers: [VCB, BID, ...]})
    Send("worker", {sector: "Steel",       tickers: [HPG, HSG, ...]})
    Send("worker", {sector: "Real Estate", tickers: [VHM, VIC, ...]})

  LangGraph runs all 3 workers in parallel.
  Each worker returns its sector report.
  All reports are collected into state["worker_results"].
  Then aggregator runs once with all results.
```

### The state flows through

```
  State key            Set by              Read by
  ─────────            ──────              ───────
  messages             user input          supervisor, aggregator
  market_snapshot      initial fetch       supervisor, workers
  subtasks             supervisor          fan_out (router)
  worker_results       workers             aggregator, reviewer
  analysis             aggregator          reviewer
  review_result        reviewer            review_router (router)
  review_feedback      reviewer            aggregator (on reject)
  final_report         reviewer/end        returned to user
```

---

## File Reference

| File | Purpose |
|---|---|
| `aipriceaction-terminal/src/aipriceaction_terminal/cli_commands.py` | `cmd_analyze()` — analyze command handler |
| `aipriceaction-terminal/src/aipriceaction_terminal/deep_research.py` | `run_deep_research()` — multi-agent pipeline with LangGraph |
| `aipriceaction-terminal/src/aipriceaction_terminal/agents/tools.py` | Tool registry — `get_live_data`, `get_ohlcv_data`, `get_performers`, etc. |
| `aipriceaction-terminal/src/aipriceaction_terminal/agents/personas.py` | Agent personas — system prompts and behavior config |
| `aipriceaction-terminal/src/aipriceaction_terminal/agents/agent.py` | `AgentSession` — streaming, retry, memory |
| `aipriceaction-terminal/src/aipriceaction_terminal/agents/callbacks.py` | Stream event types (TOKEN, THINKING, TOOL_CALL, etc.) |
| `sdk/aipriceaction-python/src/aipriceaction/context.py` | `AIContextBuilder` — assembles context for analyze |
| `sdk/aipriceaction-python/src/aipriceaction/system.py` | System prompt templates (EN/VN) |
