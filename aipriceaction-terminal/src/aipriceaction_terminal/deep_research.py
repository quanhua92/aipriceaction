"""Multi-agent deep research pipeline adapted for CLI use.

Adapted from examples/multi_agent.py into a proper importable module.
Runs supervisor -> parallel workers -> aggregator -> reviewer pipeline.
"""

from __future__ import annotations

import json
import operator
import sys
import tempfile
import time
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


def _ensure_clients(lang: str = "en") -> tuple[AIPriceAction, AIContextBuilder]:
    global _client, _builder
    if _client is None:
        _client = AIPriceAction()
    if _builder is None or _builder._lang != lang:
        _builder = AIContextBuilder(lang=lang)
    return _client, _builder


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
    tickers = client.get_tickers(source=source)
    if not tickers:
        return "No tickers found."

    lines = [f"Available tickers (source={source or 'all'}), total: {len(tickers)}"]
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
        "supervisor": """You are a research supervisor for Vietnamese stock market analysis.
Break research questions into 3-5 sector subtasks.
You MUST include these 3 sectors: Banking, Securities, Real Estate.
Pick 0-2 additional sectors based on market activity.

For each sector: select ONLY the top 10 most representative tickers based on the snapshot.
Call the `create_subtasks` tool with your subtasks.""",

        "worker_role": """## Your Role
You are a sector analyst for the Vietnamese stock market.

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
        "supervisor": """Bạn là giám đốc nghiên cứu phân tích thị trường chứng khoán Việt Nam.
Phân tích câu hỏi thành 3-5 nhiệm vụ ngành.
Bắt buộc gồm: Ngân hàng, Chứng khoán, Bất động sản.
Chọn thêm 0-2 ngành dựa trên hoạt động thị trường.
Mỗi ngành: tối đa 10 mã đại diện nhất.
Gọi tool `create_subtasks` với các nhiệm vụ.""",

        "worker_role": """## Vai Trò
Bạn là chuyên gia phân tích ngành thị trường chứng khoán Việt Nam.

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


def _invoke_with_retry(llm_call, retries: int = 5, base_delay: float = 10.0):
    """Invoke an LLM call with retry on transient API errors."""
    transient = ("429", "500", "502", "503", "504", "timeout", "connection", "overloaded")
    for attempt in range(retries):
        try:
            return llm_call()
        except Exception as e:
            err_str = str(e).lower()
            is_transient = any(code in err_str for code in transient)
            if is_transient and attempt < retries - 1:
                delay = base_delay * (2 ** attempt)
                delay = min(delay, 60)
                print(f"    [retry] {type(e).__name__}: {str(e)[:80]} (attempt {attempt + 1}/{retries}, wait {delay:.0f}s)", flush=True)
                time.sleep(delay)
            else:
                raise


def _run_agent_with_tools(system_prompt: str, user_message: str, label: str = "", retries: int = 5) -> str:
    """Run a create_agent with tools, retry on rate-limit, return final text."""
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
            for event in agent.stream(
                {"messages": [HumanMessage(content=user_message)]},
                stream_mode="updates",
            ):
                for _node_name, update in event.items():
                    for msg in update.get("messages", []):
                        msg_type = type(msg).__name__
                        if msg_type == "AIMessage" and getattr(msg, "tool_calls", None):
                            for tc in msg.tool_calls:
                                print(f"  [{label}] [tool call] {tc['name']}({tc['args']})", flush=True)
                        elif msg_type == "ToolMessage":
                            preview = msg.content[:150].replace("\n", " ")
                            print(f"  [{label}] [tool result] {preview}...", flush=True)
                        elif msg_type == "AIMessage" and msg.content:
                            result_parts.append(msg.content)
            result_text = max(result_parts, key=len) if result_parts else ""
            return result_text
        except Exception as e:
            last_error = e
            if "429" in str(e) and attempt < retries - 1:
                delay = min(10.0 * (2 ** attempt), 60)
                print(f"  [{label}] [retry] Rate limited, wait {delay:.0f}s (attempt {attempt + 1}/{retries})", flush=True)
                time.sleep(delay)
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


def _build_graph(checkpointer=None, lang: str = "en"):
    """Build the multi-agent graph with parallel fan-out."""
    _P = _PROMPTS[lang]
    llm = ChatOpenAI(
        api_key=settings.openai_api_key,
        base_url=settings.openai_base_url,
        model=settings.openai_model,
    )

    def supervisor_node(state: OverallState) -> dict:
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

        response = _invoke_with_retry(lambda: supervisor.invoke([
            SystemMessage(content=_P["supervisor"]),
            HumanMessage(content=user_message),
        ]))

        subtasks_data = None
        if getattr(response, "tool_calls", None):
            for tc in response.tool_calls:
                if tc["name"] == "create_subtasks":
                    subtasks_data = tc["args"]["subtasks"]

        if not subtasks_data:
            raise ValueError(f"Supervisor did not call create_subtasks tool. Response: {(response.content or '')[:300]}")

        subtasks = []
        for st in subtasks_data:
            sector = st.get("sector", "Unknown")
            tickers = st.get("tickers", st.get("ticker_list", []))
            tickers = tickers[:10]
            instruction = st.get("instruction", f"Analyze {sector} sector")
            subtasks.append(Subtask(sector=sector, tickers=tickers, instruction=instruction))

        print(f"[Supervisor] Decomposed into {len(subtasks)} subtasks:", flush=True)
        for st in subtasks:
            print(f"  - {st['sector']}: {', '.join(st['tickers'])}", flush=True)
        print(flush=True)

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

    def worker_node(state: dict) -> dict:
        sector = state["sector"]
        tickers = state["tickers"]

        print(f"  [Worker:{sector}] Starting analysis for {', '.join(tickers)}...", flush=True)

        try:
            system_prompt = get_system_prompt(lang) + "\n\n" + _P["worker_role"].format(
                instruction=state["instruction"],
            )

            result_text = _run_agent_with_tools(
                system_prompt=system_prompt,
                user_message=state["messages"][0].content if state["messages"] else state["instruction"],
                label=f"Worker:{sector}",
            )

            print(f"  [Worker:{sector}] Analysis complete ({len(result_text):,} chars)", flush=True)

            return {"worker_results": [WorkerResult(
                sector=sector, tickers=tickers, analysis=result_text,
            )]}

        except Exception as e:
            print(f"  [Worker:{sector}] ERROR: {e}", flush=True)
            return {"worker_results": [WorkerResult(
                sector=sector, tickers=tickers,
                analysis=f"[Analysis failed for {sector}: {e}]",
            )]}

    def aggregator_node(state: OverallState) -> dict:
        results = state["worker_results"]
        round_num = state.get("review_round", 0)
        print(f"[Aggregator] Synthesizing {len(results)} sector reports (round {round_num + 1})...", flush=True)

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

        response = _invoke_with_retry(lambda: llm.invoke([
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_message),
        ]))
        content = response.content or ""

        print(f"[Aggregator] Analysis synthesized ({len(content):,} chars)", flush=True)
        return {"analysis": content, "review_round": round_num}

    MAX_REVIEW_ROUNDS = 3

    def reviewer_node(state: OverallState) -> dict:
        round_num = state.get("review_round", 0)
        label = f"Reviewer (round {round_num + 1})"
        print(f"[{label}] Checking data integrity...", flush=True)

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
        response = _invoke_with_retry(lambda: reviewer.invoke([
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_message),
        ]))

        for tc in (response.tool_calls or []):
            name = tc["name"]
            args = tc["args"]
            if name == "approve_report":
                print(f"[{label}] APPROVED", flush=True)
                return {
                    "review_result": "approve",
                    "review_feedback": "",
                    "final_report": state.get("analysis", ""),
                }
            elif name == "reject_report":
                feedback = args.get("feedback", "")
                print(f"[{label}] REJECTED:\n{feedback[:500]}", flush=True)
                return {
                    "review_result": "reject",
                    "review_feedback": feedback,
                }

        content = (response.content or "").strip()
        print(f"[{label}] NO TOOL CALLED, treating as reject", flush=True)
        return {
            "review_result": "reject",
            "review_feedback": content or "Reviewer did not call approve_report or reject_report tool.",
        }

    def review_router(state: OverallState) -> str:
        if state.get("review_result") == "approve":
            return "end"
        if state.get("review_round", 0) >= MAX_REVIEW_ROUNDS - 1:
            print("[Reviewer] Max rounds reached, accepting current output", flush=True)
            return "end"
        return "aggregator"

    def end_node(state: OverallState) -> dict:
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


# -- Questions --


_DEFAULT_QUESTIONS = {
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
}


# -- Public API --


def run_deep_research(
    question: str = "",
    resume_id: str | None = None,
    output_file: str | None = None,
    lang: str | None = None,
) -> None:
    """Run the multi-agent deep research pipeline.

    Args:
        question: Research question. Uses default if empty.
        resume_id: Checkpoint session ID to resume from.
        output_file: Save final report to this file path.
        lang: Override language (defaults to settings.ai_context_lang).
    """
    started_at = time.time()
    effective_lang = lang or settings.ai_context_lang

    if not settings.openai_api_key:
        print("Error: OPENAI_API_KEY is not set. Set it via environment variable or .env file.", file=sys.stderr)
        import sys
        sys.exit(1)

    print("# AIPriceAction Multi-Agent Research", flush=True)
    print(flush=True)
    print(f"  Model:    {settings.openai_model}", flush=True)
    print(f"  Base URL: {settings.openai_base_url}", flush=True)
    print(f"  Started:  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}", flush=True)
    print(f"  Lang:     {effective_lang}", flush=True)
    if resume_id:
        print(f"  Resume:   {resume_id}", flush=True)
    print(flush=True)
    print("---", flush=True)
    print(flush=True)

    # Ensure clients are initialized
    _ensure_clients(effective_lang)

    if not resume_id:
        print("[1] Fetching market snapshot (all VN tickers, latest bar)...", flush=True)
        _, builder = _ensure_clients(effective_lang)
        market_snapshot = builder.build(
            source="vn",
            interval="1D",
            limit=1,
            reference_ticker=None,
            include_system_prompt=False,
        )
        client, _ = _ensure_clients(effective_lang)
        tickers = client.get_tickers(source="vn")
        print(f"    Tickers: {len(tickers)}", flush=True)
        print(flush=True)

    effective_question = question or _DEFAULT_QUESTIONS.get(effective_lang, _DEFAULT_QUESTIONS["en"])

    print(f"[2] Starting multi-agent research...", flush=True)
    print(f"    Question: {effective_question}", flush=True)
    print(flush=True)
    print("---", flush=True)
    print(flush=True)

    checkpointer = PersistentCheckpointSaver(
        session_id=resume_id,
        base_dir=Path(tempfile.gettempdir()) / "aipriceaction-checkpoints",
        callbacks=[extract_worker_results],
    )
    print(f"    Session: {checkpointer.session_id}", flush=True)
    print(f"    Folder:  {checkpointer.session_dir}", flush=True)
    print(flush=True)

    graph = _build_graph(checkpointer=checkpointer, lang=effective_lang)

    if resume_id:
        print("    Resuming from checkpoint...", flush=True)
        print(flush=True)
        result = graph.invoke(
            None,
            config={
                "recursion_limit": 50,
                "configurable": {"thread_id": checkpointer.session_id},
            },
        )
    else:
        result = graph.invoke(
            {
                "messages": [HumanMessage(content=effective_question)],
                "market_snapshot": market_snapshot,
            },
            config={
                "recursion_limit": 50,
                "configurable": {"thread_id": checkpointer.session_id},
            },
        )

    print("---", flush=True)
    print(flush=True)
    print("## [3] FINAL REPORT", flush=True)
    print(flush=True)
    print("---", flush=True)
    print(flush=True)

    report = result["final_report"]
    print(report, flush=True)
    print(flush=True)
    print("---", flush=True)
    print(flush=True)

    elapsed = time.time() - started_at
    print(f"[4] Done in {elapsed:.1f}s | Checkpoint: {checkpointer.session_dir}", flush=True)

    if output_file:
        output_path = Path(output_file).expanduser()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(report, encoding="utf-8")
        print(f"Report saved to: {output_path}", flush=True)
