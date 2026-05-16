"""Multi-agent deep research pipeline adapted for CLI and TUI use.

Adapted from examples/multi_agent.py into a proper importable module.
Runs supervisor -> parallel workers -> aggregator -> reviewer pipeline.
All public functions are async to avoid blocking the TUI event loop.
"""

from __future__ import annotations

import asyncio
import sys
import json
import operator

import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any

from langchain.agents import create_agent
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.graph import START, END, StateGraph, add_messages
from langgraph.types import Send
from typing_extensions import TypedDict

from aipriceaction import AIPriceAction, AIContextBuilder
from aipriceaction.checkpoint import PersistentCheckpointSaver
from aipriceaction.settings import settings
from aipriceaction.system import get_system_prompt


# -- Shared client (reuses disk cache across all agents) --

_client: AIPriceAction | None = None
_builder: AIContextBuilder | None = None
_source: str = "vn"


def _ensure_clients(lang: str = "en") -> tuple[AIPriceAction, AIContextBuilder]:
    global _client, _builder
    if _client is None:
        _client = AIPriceAction()
    if _builder is None or _builder._lang != lang:
        _builder = AIContextBuilder(lang=lang)
    return _client, _builder


def _ensure_source(source: str) -> None:
    global _source
    _source = source


_SOURCE_CONFIG: dict[str, dict[str, str]] = {
    "vn": {
        "market_name_en": "Vietnamese stock market",
        "market_name_vn": "thị trường chứng khoán Việt Nam",
        "mandatory_sectors_en": "Banking, Securities, Real Estate",
        "mandatory_sectors_vn": "Ngân hàng, Chứng khoán, Bất động sản",
        "optional_hint_en": "Pick 0-2 additional sectors based on market activity.",
        "optional_hint_vn": "Chọn thêm 0-2 ngành dựa trên hoạt động thị trường.",
    },
    "crypto": {
        "market_name_en": "cryptocurrency market",
        "market_name_vn": "thị trường tiền mã hóa",
        "mandatory_sectors_en": "Layer 1 (BTC, ETH, SOL), DeFi, AI tokens",
        "mandatory_sectors_vn": "Layer 1 (BTC, ETH, SOL), DeFi, AI tokens",
        "optional_hint_en": "Pick 0-2 additional sectors (e.g., Memecoins, Gaming, RWA).",
        "optional_hint_vn": "Chọn thêm 0-2 ngành (ví dụ: Memecoins, Gaming, RWA).",
    },
    "yahoo": {
        "market_name_en": "global stock market",
        "market_name_vn": "thị trường chứng khoán toàn cầu",
        "mandatory_sectors_en": "Technology, Financials, Energy",
        "mandatory_sectors_vn": "Công nghệ, Tài chính, Năng lượng",
        "optional_hint_en": "Pick 0-2 additional sectors (e.g., Healthcare, Consumer, Industrials).",
        "optional_hint_vn": "Chọn thêm 0-2 ngành (ví dụ: Y tế, Tiêu dùng, Công nghiệp).",
    },
    "sjc": {
        "market_name_en": "commodities market",
        "market_name_vn": "thị trường hàng hóa",
        "mandatory_sectors_en": "Gold / Precious Metals",
        "mandatory_sectors_vn": "Vàng / Kim loại quý",
        "optional_hint_en": "",
        "optional_hint_vn": "",
    },
}


# -- Tools --


@tool
def create_subtasks(subtasks: list[dict]) -> str:
    """Create research subtasks for sector analysis. Each subtask must be a dict with exactly these 3 keys: "sector" (str), "tickers" (list[str]), "instruction" (str). Provide 3-5 subtasks."""
    return json.dumps({"subtasks": subtasks})


@tool
def get_ohlcv_data(ticker: str, interval: str = "1D", limit: int = 20) -> str:
    """Fetch OHLCV data for a ticker. Returns formatted context with MA indicators."""
    _, builder = _ensure_clients()
    try:
        ctx = builder.build(
            ticker=ticker,
            interval=interval,
            limit=limit,
            reference_ticker=None,
            include_system_prompt=False,
            source=_source,
        )
    except Exception as e:
        return f"Error fetching {ticker}: {e}"
    if not ctx.strip():
        return f"No data found for {ticker} ({interval})."
    return ctx


@tool
def approve_report() -> str:
    """Approve the report. Call this when ALL data integrity checks pass."""
    return "APPROVED"


@tool
def reject_report(feedback: str) -> str:
    """Reject the report. Call this when you find data integrity issues. Provide specific feedback."""
    return f"REJECTED: {feedback}"


@tool
def get_ticker_list(source: str | None = None) -> str:
    """List available ticker symbols and metadata."""
    client, _ = _ensure_clients()
    effective_source = source or _source
    tickers = client.get_tickers(source=effective_source)
    if not tickers:
        return "No tickers found."

    lines = [f"Available tickers (source={effective_source}), total: {len(tickers)}"]
    symbols = [t.ticker for t in tickers]
    lines.append("Symbols: " + ", ".join(symbols))
    return "\n".join(lines)


# -- State --


class Subtask(TypedDict):
    sector: str
    tickers: list[str]
    instruction: str


class WorkerResult(TypedDict):
    sector: str
    tickers: list[str]
    analysis: str


class OverallState(TypedDict):
    messages: Annotated[list, add_messages]
    market_snapshot: str
    subtasks: list[Subtask]
    worker_results: Annotated[list[WorkerResult], operator.add]
    analysis: str
    review_result: str
    review_feedback: str
    review_round: int
    final_report: str


# -- Prompts --

_PROMPTS = {
    "en": {
        "supervisor": """You are a research supervisor for {market_name} analysis.
Break research questions into 3-5 sector subtasks.
You MUST include these sectors: {mandatory_sectors}.
{optional_hint}

For each sector: select ONLY the top 10 most representative tickers based on the snapshot.
Call the `create_subtasks` tool with your subtasks.""",

        "worker_role": """## Your Role
You are a sector analyst for the {market_name}.

## Instructions
{instruction}

## Research Workflow — TOOL CALLS ARE MANDATORY
Step 1: Call `get_ohlcv_data` with limit=20 for EACH ticker.
Step 2: WAIT for all tool results before writing any analysis.
Step 3: Base your analysis ONLY on the tool results.
Step 4: Assess trend direction, VPA signals, MA score momentum, volume.
Step 5: Provide per-ticker assessment, sector ranking, key risk factors.
Step 6: Include the investment disclaimer at the end.

FAILURE TO CALL TOOLS = INVALID RESPONSE.""",

        "aggregator_system": """You are a senior investment strategist. Synthesize sector reports into unified analysis.
1. Read ALL sector reports carefully.
2. Cross-reference findings across sectors.
3. Build a unified multi-sector ranking table.
4. Identify cross-sector rotation patterns.
5. Highlight key opportunities and risks.
6. Do NOT fetch additional data — use worker reports only.
7. Include the investment disclaimer at the end.
8. Quote numbers EXACTLY as workers reported them. Do NOT add stocks not in worker reports.""",

        "aggregator_user": """## Sector Analysis Reports
{sector_reports}

## Original Question
{question}""",

        "reviewer_system": """You are a data quality reviewer. Fact-check the aggregator's report against worker reports.

Check List:
1. Phantom stocks: tickers not in worker reports?
2. MA score fidelity: pick 3-5 scores and verify.
3. Table completeness: each table only has analyzed tickers?
4. Missing data: numbers not in any worker report?

Call `approve_report` or `reject_report(feedback="...")`.""",

        "reviewer_user": """## Worker Reports (source of truth)
{worker_reports}

## Aggregator Output (to review)
{analysis}""",
    },
    "vn": {
        "supervisor": """Bạn là giám đốc nghiên cứu phân tích {market_name}.
Phân tích câu hỏi thành 3-5 nhiệm vụ ngành.
Bắt buộc gồm: {mandatory_sectors}.
{optional_hint}
Mỗi ngành: tối đa 10 mã đại diện nhất.
Gọi tool `create_subtasks` với các nhiệm vụ.""",

        "worker_role": """## Vai Trò
Bạn là chuyên gia phân tích ngành {market_name}.

## Hướng Dẫn
{instruction}

## Quy Trình Nghiên Cứu — GỌI TOOL LÀ BẮT BUỘC
Bước 1: Gọi `get_ohlcv_data` với limit=20 cho MỖI mã.
Bước 2: ĐỢI kết quả tool trước khi viết phân tích.
Bước 3: Phân tích CHỈ dựa trên kết quả tool.
Bước 4: Đánh giá xu hướng, tín hiệu VPA, động lực MA score, khối lượng.
Bước 5: Đánh giá từng mã, xếp hạng ngành, rủi ro chính.
Bước 6: Bao gồm tuyên bố miễn trách nhiệm đầu tư.

KHÔNG GỌI TOOL = PHẢN HỒI KHÔNG HỢP LỆ.""",

        "aggregator_system": """Bạn là chiến lược gia đầu tư cấp cao. Tổng hợp báo cáo ngành thành phân tích thống nhất.
1. Đọc KỸ tất cả báo cáo ngành.
2. Chéo tham khảo phát hiện giữa các ngành.
3. Xây dựng bảng xếp hạng đa ngành thống nhất.
4. Xác định mô hình luân chuyển liên ngành.
5. Nhấn mạnh cơ hội và rủi ro chính.
6. KHÔNG tải thêm dữ liệu — chỉ dùng báo cáo nhân viên.
7. Bao gồm tuyên bố miễn trách nhiệm đầu tư.
8. Trích dẫn số liệu NGUYÊN VĂN từ báo cáo nhân viên.""",

        "aggregator_user": """## Báo Cáo Phân Tích Ngành
{sector_reports}

## Câu Hỏi Gốc
{question}""",

        "reviewer_system": """Bạn là người kiểm tra chất lượng dữ liệu. Kiểm tra báo cáo tổng hợp so với báo cáo nhân viên.

Danh Sách Kiểm Tra:
1. Mã cổ phiếu ma: mã không có trong báo cáo nhân viên?
2. Độ chính xác điểm MA: chọn 3-5 điểm và xác nhận.
3. Đầy đủ bảng: mỗi bảng chỉ chứa mã đã phân tích?
4. Dữ liệu thiếu: số liệu không có trong báo cáo nhân viên?

Gọi `approve_report` hoặc `reject_report(feedback="...")`.""",

        "reviewer_user": """## Báo Cáo Nhân Viên (nguồn sự thật)
{worker_reports}

## Đầu Ra Người Tổng Hợp (cần kiểm tra)
{analysis}""",
    },
}

# -- Helpers --


async def _invoke_with_retry(llm_call, retries: int = 5, base_delay: float = 10.0, output: Callable[[str], None] | None = None):
    """Invoke an LLM call with retry on transient API errors."""
    _out = output or print
    transient = ("429", "500", "502", "503", "504", "timeout", "connection", "overloaded")
    for attempt in range(retries):
        try:
            return await llm_call()
        except Exception as e:
            err_str = str(e).lower()
            is_transient = any(code in err_str for code in transient)
            if is_transient and attempt < retries - 1:
                delay = base_delay * (2 ** attempt)
                delay = min(delay, 60)
                _out(f"    [retry] {type(e).__name__}: {str(e)[:80]} (attempt {attempt + 1}/{retries}, wait {delay:.0f}s)")
                await asyncio.sleep(delay)
            else:
                raise


async def _run_agent_with_tools(system_prompt: str, user_message: str, label: str = "", retries: int = 5, output: Callable[[str], None] | None = None) -> str:
    """Run a create_agent with tools, retry on rate-limit, return final text."""
    _out = output or print
    llm = ChatOpenAI(
        api_key=settings.openai_api_key,
        base_url=settings.openai_base_url,
        model=settings.openai_model,
    )
    agent = create_agent(
        llm,
        [get_ticker_list, get_ohlcv_data],
        system_prompt=system_prompt,
    )

    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            result_parts = []
            async for event in agent.astream(
                {"messages": [HumanMessage(content=user_message)]},
                stream_mode="updates",
            ):
                for _node_name, update in event.items():
                    for msg in update.get("messages", []):
                        msg_type = type(msg).__name__
                        if msg_type == "AIMessage" and getattr(msg, "tool_calls", None):
                            for tc in msg.tool_calls:
                                _out(f"  [{label}] [tool call] {tc['name']}({tc['args']})")
                        elif msg_type == "ToolMessage":
                            preview = msg.content[:150].replace("\n", " ")
                            _out(f"  [{label}] [tool result] {preview}...")
                        elif msg_type == "AIMessage" and msg.content:
                            result_parts.append(msg.content)
            result_text = max(result_parts, key=len) if result_parts else ""
            return result_text
        except Exception as e:
            last_error = e
            if "429" in str(e) and attempt < retries - 1:
                delay = min(10.0 * (2 ** attempt), 60)
                _out(f"  [{label}] [retry] Rate limited, wait {delay:.0f}s (attempt {attempt + 1}/{retries})")
                await asyncio.sleep(delay)
    assert last_error is not None
    raise last_error


# -- Checkpoint callback --


def extract_worker_results(channel_values: dict[str, Any], session_dir: Path) -> None:
    """Extract per-sector worker analysis to .md files."""
    for wr in channel_values.get("worker_results", []):
        sector = wr.get("sector", "unknown")
        analysis = wr.get("analysis", "")
        safe_name = sector.replace(" ", "_").replace("/", "-")[:60]
        (session_dir / f"worker_{safe_name}.md").write_text(
            f"# Sector: {sector}\n\n{analysis}\n"
        )

    analysis = channel_values.get("analysis", "")
    if analysis:
        round_num = channel_values.get("review_round", 0)
        suffix = f"_round{round_num}" if round_num > 0 else ""
        (session_dir / f"aggregator_output{suffix}.md").write_text(analysis)

    final = channel_values.get("final_report", "")
    if final:
        (session_dir / "final_report.md").write_text(final)


# -- Graph nodes --


def _build_graph(checkpointer=None, lang: str = "en", source: str = "vn", output: Callable[[str], None] | None = None):
    """Build the multi-agent graph with parallel fan-out."""
    _P = _PROMPTS[lang]
    _cfg = _SOURCE_CONFIG.get(source, _SOURCE_CONFIG["vn"])
    _out = output or print
    _ensure_source(source)
    llm = ChatOpenAI(
        api_key=settings.openai_api_key,
        base_url=settings.openai_base_url,
        model=settings.openai_model,
    )

    async def supervisor_node(state: OverallState) -> dict:
        user_question = ""
        for msg in state["messages"]:
            if isinstance(msg, HumanMessage):
                user_question = msg.content
                break

        supervisor = llm.bind_tools([create_subtasks])
        user_message = (
            f"## Market Snapshot\n{state['market_snapshot']}\n\n"
            f"## User Question\n{user_question}"
        )

        # Retry up to 3 times if the model doesn't call the tool
        max_tool_retries = 3
        subtasks_data = None
        response = None
        for attempt in range(max_tool_retries):
            extra = ""
            if attempt > 0:
                extra = "\n\nIMPORTANT: You MUST call the `create_subtasks` tool. Do NOT respond with plain text."
            response = await _invoke_with_retry(lambda: supervisor.ainvoke([
                SystemMessage(content=_P["supervisor"].format(
                    market_name=_cfg[f"market_name_{lang}"],
                    mandatory_sectors=_cfg[f"mandatory_sectors_{lang}"],
                    optional_hint=_cfg[f"optional_hint_{lang}"],
                )),
                HumanMessage(content=user_message + extra),
            ]), output=output)

            if getattr(response, "tool_calls", None):
                for tc in response.tool_calls:
                    if tc["name"] == "create_subtasks":
                        raw = tc["args"].get("subtasks")
                        if isinstance(raw, str):
                            try:
                                subtasks_data = json.loads(raw)
                            except json.JSONDecodeError:
                                subtasks_data = None
                        elif isinstance(raw, list):
                            subtasks_data = raw
                        if subtasks_data:
                            break

            if subtasks_data:
                break
            _out(f"[Supervisor] No tool call on attempt {attempt + 1}/{max_tool_retries}, retrying...")

        if not subtasks_data:
            raise ValueError(f"Supervisor did not call create_subtasks tool after {max_tool_retries} attempts. Response: {(response.content or '')[:300]}")

        # Fetch valid tickers for validation
        client, _ = _ensure_clients(lang)
        valid_tickers = {t.ticker.upper() for t in client.get_tickers(source=_source)}

        subtasks = []
        for st in subtasks_data:
            if isinstance(st, str):
                try:
                    st = json.loads(st)
                except json.JSONDecodeError:
                    continue
            sector = st.get("sector", "Unknown")
            tickers = st.get("tickers", st.get("ticker_list", []))
            validated = [t for t in tickers if t.upper() in valid_tickers][:10]
            if not validated:
                _out(f"[Supervisor] WARNING: {sector} — no valid tickers, skipping")
                continue
            dropped = [t for t in tickers if t.upper() not in valid_tickers]
            if dropped:
                _out(f"[Supervisor] {sector} — dropped invalid tickers: {', '.join(dropped)}")
            instruction = st.get("instruction", f"Analyze {sector} sector")
            subtasks.append(Subtask(sector=sector, tickers=validated, instruction=instruction))

        _out(f"[Supervisor] Decomposed into {len(subtasks)} subtasks:")
        for st in subtasks:
            _out(f"  - {st['sector']}: {', '.join(st['tickers'])}")
        _out("")

        return {"subtasks": subtasks}

    def fan_out(state: OverallState) -> list[Send]:
        return [
            Send("worker", {
                "messages": [HumanMessage(
                    content=f"{state['market_snapshot']}\n\n---\n\n{st['instruction']}"
                )],
                "market_snapshot": state["market_snapshot"],
                "sector": st["sector"],
                "tickers": st["tickers"],
                "instruction": st["instruction"],
            })
            for st in state["subtasks"]
        ]

    async def worker_node(state: dict) -> dict:
        sector = state["sector"]
        tickers = state["tickers"]

        _out(f"  [Worker:{sector}] Starting analysis for {', '.join(tickers)}...")

        try:
            system_prompt = get_system_prompt(lang) + "\n\n" + _P["worker_role"].format(
                market_name=_cfg[f"market_name_{lang}"],
                instruction=state["instruction"],
            )

            result_text = await _run_agent_with_tools(
                system_prompt=system_prompt,
                user_message=state["messages"][0].content if state["messages"] else state["instruction"],
                label=f"Worker:{sector}",
                output=output,
            )

            _out(f"  [Worker:{sector}] Analysis complete ({len(result_text):,} chars)")

            return {"worker_results": [WorkerResult(
                sector=sector, tickers=tickers, analysis=result_text,
            )]}

        except Exception as e:
            _out(f"  [Worker:{sector}] ERROR: {e}")
            return {"worker_results": [WorkerResult(
                sector=sector, tickers=tickers,
                analysis=f"[Analysis failed for {sector}: {e}]",
            )]}

    async def aggregator_node(state: OverallState) -> dict:
        results = state["worker_results"]
        round_num = state.get("review_round", 0)
        _out(f"[Aggregator] Synthesizing {len(results)} sector reports (round {round_num + 1})...")

        user_question = ""
        for msg in state["messages"]:
            if isinstance(msg, HumanMessage):
                user_question = msg.content
                break

        sector_reports = ""
        for wr in results:
            sector_reports += f"\n### {wr['sector']} ({', '.join(wr['tickers'])})\n\n{wr['analysis']}\n\n"

        feedback = state.get("review_feedback", "")
        feedback_section = ""
        if feedback:
            feedback_section = f"\n\n## Reviewer Feedback (round {round_num})\nFix these issues:\n{feedback}\n"

        system_prompt = (
            get_system_prompt(lang, include_data_policy=False, include_analysis_framework=True)
            + "\n\n"
            + _P["aggregator_system"]
        )
        user_message = _P["aggregator_user"].format(
            sector_reports=sector_reports,
            question=user_question,
        ) + feedback_section

        response = await _invoke_with_retry(lambda: llm.ainvoke([
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_message),
        ]), output=output)
        content = response.content or ""

        _out(f"[Aggregator] Analysis synthesized ({len(content):,} chars)")
        return {"analysis": content, "review_round": round_num + 1}

    MAX_REVIEW_ROUNDS = 5

    async def reviewer_node(state: OverallState) -> dict:
        round_num = state.get("review_round", 0)
        label = f"Reviewer (round {round_num})"
        _out(f"[{label}] Checking data integrity...")

        worker_reports = ""
        for wr in state["worker_results"]:
            worker_reports += f"\n### {wr['sector']} ({', '.join(wr['tickers'])})\n\n{wr['analysis']}\n\n"

        system_prompt = (
            get_system_prompt(lang, include_data_policy=False, include_analysis_framework=False)
            + "\n\n"
            + _P["reviewer_system"]
        )
        user_message = _P["reviewer_user"].format(
            worker_reports=worker_reports,
            analysis=state.get("analysis", ""),
        )

        reviewer = llm.bind_tools([approve_report, reject_report])
        response = await _invoke_with_retry(lambda: reviewer.ainvoke([
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_message),
        ]), output=output)

        for tc in (response.tool_calls or []):
            name = tc["name"]
            args = tc["args"]
            if name == "approve_report":
                _out(f"[{label}] APPROVED")
                return {
                    "review_result": "approve",
                    "review_feedback": "",
                    "final_report": state.get("analysis", ""),
                }
            elif name == "reject_report":
                feedback = args.get("feedback", "")
                _out(f"[{label}] REJECTED:\n{feedback[:500]}")
                return {
                    "review_result": "reject",
                    "review_feedback": feedback,
                }

        content = (response.content or "").strip()
        _out(f"[{label}] NO TOOL CALLED, treating as reject")
        return {
            "review_result": "reject",
            "review_feedback": content or "Reviewer did not call approve_report or reject_report tool.",
        }

    def review_router(state: OverallState) -> str:
        if state.get("review_result") == "approve":
            return "end"
        if state.get("review_round", 0) >= MAX_REVIEW_ROUNDS:
            _out("[Reviewer] Max rounds reached, accepting current output")
            return "end"
        return "aggregator"

    def end_node(state: OverallState) -> dict:
        # If reviewer never approved (max rounds), use the last analysis as final report
        if not state.get("final_report"):
            return {"final_report": state.get("analysis", "")}
        return {}

    # Build graph
    graph = StateGraph(OverallState)
    graph.add_node("supervisor", supervisor_node)
    graph.add_node("worker", worker_node)
    graph.add_node("aggregator", aggregator_node)
    graph.add_node("reviewer", reviewer_node)
    graph.add_node("end", end_node)

    graph.add_edge(START, "supervisor")
    graph.add_conditional_edges("supervisor", fan_out, ["worker"])
    graph.add_edge("worker", "aggregator")
    graph.add_edge("aggregator", "reviewer")
    graph.add_conditional_edges("reviewer", review_router, ["aggregator", "end"])
    graph.add_edge("end", END)

    return graph.compile(checkpointer=checkpointer)


_PIPELINE_STEPS = """## Pipeline Steps

  [1] Fetch market snapshot — tickers from selected source, latest daily bar
  [2] Supervisor agent — decomposes question into sector-specific subtasks
  [3] Worker agents (parallel) — research each subtask with tool-calling (get_ohlcv_data, get_live_data, get_ticker_list)
  [4] Aggregator — merges worker results into unified draft report (retries up to 3 rounds on reviewer feedback)
  [5] Reviewer — checks data integrity, approves or rejects with feedback"""


# -- Questions --


_DEFAULT_QUESTIONS: dict[str, dict[str, str]] = {
    "vn": {
        "en": (
            "Provide a comprehensive market overview of the Vietnamese stock market. "
            "Use the market snapshot to identify the most active sectors and tickers, "
            "then research 3-5 sectors in depth (must include Banking, Securities, Real Estate) "
            "with full OHLCV data. "
            "For each sector: select only the top 10 most representative tickers, "
            "assess trend direction, VPA signals, MA score momentum, "
            "volume confirmation, and identify sector leaders vs laggards. "
            "Then synthesize cross-sector rotation patterns and provide a unified ranking."
        ),
        "vn": (
            "Cung cấp tổng quan thị trường chứng khoán Việt Nam toàn diện. "
            "Sử dụng bức tranh thị trường để xác định ngành và mã hoạt động mạnh nhất, "
            "sau đó nghiên cứu sâu 3-5 ngành (bắt buộc gồm Ngân hàng, Chứng khoán, Bất động sản) "
            "với dữ liệu OHLCV đầy đủ. "
            "Mỗi ngành: chỉ chọn tối đa 10 mã đại diện nhất, "
            "đánh giá xu hướng, tín hiệu VPA, động lực MA score, "
            "xác nhận khối lượng, và xác định mã dẫn đầu/lagging. "
            "Sau đó tổng hợp mô hình luân chuyển liên ngành và xếp hạng thống nhất."
        ),
    },
    "crypto": {
        "en": (
            "Provide a comprehensive market overview of the cryptocurrency market. "
            "Use the market snapshot to identify the most active sectors and tokens, "
            "then research 3-5 sectors in depth (must include Layer 1, DeFi, AI tokens) "
            "with full OHLCV data. "
            "For each sector: select only the top 10 most representative tokens, "
            "assess trend direction, VPA signals, MA score momentum, "
            "volume confirmation, and identify sector leaders vs laggards. "
            "Then synthesize cross-sector rotation patterns and provide a unified ranking."
        ),
        "vn": (
            "Cung cấp tổng quan thị trường tiền mã hóa toàn diện. "
            "Sử dụng bức tranh thị trường để xác định ngành và token hoạt động mạnh nhất, "
            "sau đó nghiên cứu sâu 3-5 ngành (bắt buộc gồm Layer 1, DeFi, AI tokens) "
            "với dữ liệu OHLCV đầy đủ. "
            "Mỗi ngành: chỉ chọn tối đa 10 token đại diện nhất, "
            "đánh giá xu hướng, tín hiệu VPA, động lực MA score, "
            "xác nhận khối lượng, và xác định token dẫn đầu/lagging. "
            "Sau đó tổng hợp mô hình luân chuyển liên ngành và xếp hạng thống nhất."
        ),
    },
    "yahoo": {
        "en": (
            "Provide a comprehensive market overview of the global stock market. "
            "Use the market snapshot to identify the most active sectors and tickers, "
            "then research 3-5 sectors in depth (must include Technology, Financials, Energy) "
            "with full OHLCV data. "
            "For each sector: select only the top 10 most representative tickers, "
            "assess trend direction, VPA signals, MA score momentum, "
            "volume confirmation, and identify sector leaders vs laggards. "
            "Then synthesize cross-sector rotation patterns and provide a unified ranking."
        ),
        "vn": (
            "Cung cấp tổng quan thị trường chứng khoán toàn cầu toàn diện. "
            "Sử dụng bức tranh thị trường để xác định ngành và mã hoạt động mạnh nhất, "
            "sau đó nghiên cứu sâu 3-5 ngành (bắt buộc gồm Công nghệ, Tài chính, Năng lượng) "
            "với dữ liệu OHLCV đầy đủ. "
            "Mỗi ngành: chỉ chọn tối đa 10 mã đại diện nhất, "
            "đánh giá xu hướng, tín hiệu VPA, động lực MA score, "
            "xác nhận khối lượng, và xác định mã dẫn đầu/lagging. "
            "Sau đó tổng hợp mô hình luân chuyển liên ngành và xếp hạng thống nhất."
        ),
    },
    "sjc": {
        "en": (
            "Provide a comprehensive market overview of the commodities market. "
            "Use the market snapshot to analyze gold and precious metals. "
            "Research the Gold / Precious Metals sector in depth with full OHLCV data. "
            "Assess trend direction, VPA signals, MA score momentum, volume confirmation. "
            "Provide a unified ranking of the available instruments."
        ),
        "vn": (
            "Cung cấp tổng quan thị trường hàng hóa toàn diện. "
            "Sử dụng bức tranh thị trường để phân tích vàng và kim loại quý. "
            "Nghiên cứu sâu ngành Vàng / Kim loại quý với dữ liệu OHLCV đầy đủ. "
            "Đánh giá xu hướng, tín hiệu VPA, động lực MA score, xác nhận khối lượng. "
            "Cung cấp xếp hạng thống nhất các công cụ có sẵn."
        ),
    },
}


def _get_default_question(lang: str, source: str) -> str:
    """Get the default research question for a given language and source."""
    source_questions = _DEFAULT_QUESTIONS.get(source, _DEFAULT_QUESTIONS["vn"])
    return source_questions.get(lang, source_questions["en"])


# -- Public API --


async def run_deep_research(
    question: str = "",
    resume_id: str | None = None,
    output_file: str | None = None,
    lang: str | None = None,
    output: Callable[[str], None] | None = None,
    run_pipeline: bool = False,
    source: str | None = None,
) -> str:
    """Run the multi-agent deep research pipeline.

    Args:
        question: Research question. Uses default if empty.
        resume_id: Checkpoint session ID to resume from.
        output_file: Save final report to this file path.
        lang: Override language (defaults to settings.ai_context_lang).
        output: Callback for progress output. Defaults to print() for CLI compat.
        run_pipeline: If False (default), fetch and print market snapshot only.
            If True, run the full multi-agent pipeline (takes 5-10 min).
        source: Data source filter (e.g. "vn", "crypto", "yahoo"). Defaults to "vn".

    Returns:
        The final report text, or market snapshot if run_pipeline is False.
    """
    started_at = time.time()
    effective_lang = lang or settings.ai_context_lang
    effective_source = source or "vn"
    _out = output or (lambda m: print(m, file=sys.stderr) if not run_pipeline else print(m))

    if not settings.openai_api_key and run_pipeline:
        _out("# AIPriceAction Multi-Agent Research (dry run)")
        _out("")
        _out("No API key configured. Cannot run the research pipeline.")
        _out("")
        _out(_PIPELINE_STEPS)
        _out("")
        _out("Run 'aipa setup' to configure your API key, then re-run:")
        _out("")
        _out("  aipa deep-research --run")
        _out("")
        return ""

    _out("# AIPriceAction Multi-Agent Research")
    _out("")
    _out(f"  Model:    {settings.openai_model}")
    _out(f"  Base URL: {settings.openai_base_url}")
    _out(f"  Started:  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    _out(f"  Lang:     {effective_lang}")
    _out(f"  Source:   {effective_source}")
    if resume_id:
        _out(f"  Resume:   {resume_id}")
    _out("")
    _out("---")
    _out("")

    # Ensure clients and source are initialized
    _ensure_clients(effective_lang)
    _ensure_source(effective_source)

    market_snapshot = ""
    if not resume_id:
        _out(f"[1] Fetching market snapshot (all {effective_source} tickers, latest bar)...")
        _, builder = _ensure_clients(effective_lang)
        market_snapshot = builder.build(
            source=effective_source,
            interval="1D",
            limit=1,
            reference_ticker=None,
            include_system_prompt=False,
        )
        client, _ = _ensure_clients(effective_lang)
        tickers = client.get_tickers(source=effective_source)
        _out(f"    Tickers: {len(tickers)}")
        _out("")

    if not run_pipeline:
        print(market_snapshot)
        print("")
        print("---")
        print("")
        print("This is a market snapshot only. To run the full multi-agent pipeline:")
        print("")
        print("  aipa deep-research --run")
        print("")
        print(_PIPELINE_STEPS)
        print("")
        return market_snapshot

    _out("[!] The full multi-agent pipeline typically takes 5-10 minutes.")
    _out("")

    effective_question = question or _get_default_question(effective_lang, effective_source)

    _out("[2] Starting multi-agent research...")
    _out(f"    Question: {effective_question}")
    _out("")
    _out("---")
    _out("")

    checkpointer = PersistentCheckpointSaver(
        session_id=resume_id,
        base_dir=Path.home() / ".aipriceaction" / "deep-research",
        callbacks=[extract_worker_results],
    )
    _out(f"    Session: {checkpointer.session_id}")
    _out(f"    Folder:  {checkpointer.session_dir}")
    _out("")

    graph = _build_graph(checkpointer=checkpointer, lang=effective_lang, source=effective_source, output=_out)

    if resume_id:
        _out("    Resuming from checkpoint...")
        _out("")
        result = await graph.ainvoke(
            None,
            config={
                "recursion_limit": 50,
                "configurable": {"thread_id": checkpointer.session_id},
            },
        )
    else:
        result = await graph.ainvoke(
            {
                "messages": [HumanMessage(content=effective_question)],
                "market_snapshot": market_snapshot,
            },
            config={
                "recursion_limit": 50,
                "configurable": {"thread_id": checkpointer.session_id},
            },
        )

    _out("---")
    _out("")
    _out("## [3] FINAL REPORT")
    _out("")
    _out("---")
    _out("")

    report = result["final_report"]
    _out(report)
    _out("")
    _out("---")
    _out("")

    elapsed = time.time() - started_at
    _out(f"Done in {elapsed:.1f}s | Checkpoint: {checkpointer.session_dir}")
    _out("")
    _out(_PIPELINE_STEPS)
    _out("")

    if output_file:
        output_path = Path(output_file).expanduser()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(report, encoding="utf-8")
        _out(f"Report saved to: {output_path}")

    return report
