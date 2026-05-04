"""Multi-agent system with LangGraph Send() for parallel sector research.

A supervisor receives a full market snapshot (latest bar for all VN tickers),
decomposes a research question into sector subtasks, fans out to parallel
worker agents (each with real tool-calling via create_agent), then an
aggregator synthesizes all results into a final cross-sector report.

Pattern:
  1. Fetch all VN tickers (limit=1) as a market snapshot
  2. Supervisor agent uses the snapshot to pick sectors and tickers
  3. Send() fans out to N worker agents simultaneously
  4. Each worker fetches detailed OHLCV data via tools
  5. Aggregator synthesizes all worker outputs into unified analysis
  6. Writer formats the analysis into a final publication-ready report

Requires OPENAI_API_KEY in .env or environment.

Usage:
    uv run python examples/multi_agent.py
"""

from __future__ import annotations

import json
import operator
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
from aipriceaction.checkpoint import PersistentCheckpointSaver, PostPutCallback
from aipriceaction.settings import settings
from aipriceaction.system import get_system_prompt

# ── Shared client (reuses disk cache across all agents) ──

_client = AIPriceAction()
_builder = AIContextBuilder(lang=settings.ai_context_lang)
LANG = settings.ai_context_lang

# ── Tools ──


@tool
def create_subtasks(subtasks: list[dict]) -> str:
    """Create research subtasks for sector analysis. Each subtask is a dict with keys: sector (str), tickers (list[str]), instruction (str). Provide 2-3 subtasks."""
    return json.dumps({"subtasks": subtasks})


@tool
def get_ohlcv_data(ticker: str, interval: str = "1D", limit: int = 30) -> str:
    """Fetch OHLCV data for a ticker. Returns formatted context with MA indicators."""
    try:
        ctx = _builder.build(
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
def get_ticker_list(source: str | None = None) -> str:
    """List available ticker symbols and metadata."""
    tickers = _client.get_tickers(source=source)
    if not tickers:
        return "No tickers found."

    lines = [f"=== Available tickers (source={source or 'all'}) ===\n"]
    lines.append(f"{'symbol':<12s}  {'name':<40s}  {'group':<30s}  {'source'}")
    lines.append("-" * 100)
    for t in tickers:
        name = (t.name or "")[:38]
        group = (t.group or "")[:28]
        lines.append(f"{t.ticker:<12s}  {name:<40s}  {group:<30s}  {t.source}")
    lines.append(f"\nTotal: {len(tickers)} tickers")
    return "\n".join(lines)


# ── State ──


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
    final_report: str


# ── Prompts (language-aware) ──

_PROMPTS = {
    "en": {
        "supervisor": """You are a research supervisor for Vietnamese stock market analysis.
Break research questions into 2-3 sector subtasks with 3 representative tickers each.
A full market snapshot (latest bar for ALL VN tickers with MA scores and change %) is provided — use it
to pick the most representative tickers per sector (e.g., highest volume, largest change, best/worst MA score).

Call the `create_subtasks` tool with your subtasks. Each subtask needs: sector name, ticker list, and specific instruction.

Common VN market sectors: Banking, Real Estate, Securities, Technology, Retail, Energy, Materials, Construction.""",

        "worker_role": """## Your Role
You are a sector analyst for the Vietnamese stock market.

## Instructions
{instruction}

## Research Workflow — TOOL CALLS ARE MANDATORY
You MUST call tools before producing any analysis. The market snapshot alone
is insufficient — it only shows the latest bar. Every response MUST include
at least one `get_ohlcv_data` tool call.

Step 1: Call `get_ohlcv_data` with limit=20 for EACH ticker in your assignment.
Step 2: WAIT for all tool results before writing any analysis.
Step 3: Base your analysis ONLY on the tool results, not the snapshot.
Step 4: Assess trend direction, VPA signals, MA score momentum, volume.
Step 5: Provide per-ticker assessment, sector ranking, key risk factors.
Step 6: Include the investment disclaimer at the end.

FAILURE TO CALL TOOLS = INVALID RESPONSE. Do NOT skip tool calls.""",

        "aggregator_system": """Current time: __CURRENT_TIME__ (ICT, UTC+7)

You are a senior investment strategist. Your job is to SYNTHESIZE
the sector reports into a single unified analysis. The worker agents have already fetched
and analyzed all the data — you must build on their findings, not start from scratch.

## Instructions
1. Read ALL sector reports carefully. They contain complete per-ticker analysis.
2. Cross-reference findings: which sectors are leading/lagging? Do MA scores agree?
3. Build a unified multi-sector ranking table from the worker findings.
4. Identify cross-sector rotation patterns and relative strength.
5. Highlight key opportunities and risks across sectors.
6. Do NOT fetch any data — use only what the worker reports provide.
7. Do NOT include the investment disclaimer.""",

        "aggregator_user": """## Market Snapshot (latest bar for all VN tickers)
{market_snapshot}

## Sector Analysis Reports (from worker agents)
{sector_reports}

## Original Question
{question}""",

        "writer_system": """Current time: __CURRENT_TIME__ (ICT, UTC+7)

You are a senior investment writer. Your job is to FORMAT the
analyst report into a clean, publication-ready document. Do NOT add new analysis or ask
for data — work exclusively with the content provided.

## Instructions
Format the analyst report into a professional final report:
1. Executive summary
2. Sector-by-sector analysis with ranking tables
3. Cross-sector rotation observations
4. Unified multi-sector ticker ranking table
5. Strategic recommendations
6. Include the investment disclaimer at the very end.
Write in a clear, structured format with headers, tables, and bullet points.""",

        "writer_user": """## Market Snapshot (latest bar for all VN tickers)
{market_snapshot}

## Analyst Report
{analysis}

## Original Question
{question}""",
    },
    "vn": {
        "supervisor": """Bạn là giám đốc nghiên cứu phân tích thị trường chứng khoán Việt Nam.
Phân tích câu hỏi thành 2-3 nhiệm vụ phân tích ngành, mỗi nhiệm vụ 3 cổ phiếu đại diện.
Bức tranh toàn thị trường (thanh gần nhất cho TẤT CẢ mã VN với MA score và % thay đổi) đã được cung cấp —
sử dụng để chọn mã đại diện nhất mỗi ngành (VD: khối lượng cao nhất, thay đổi lớn nhất, MA score tốt nhất/kém nhất).

Gọi tool `create_subtasks` với các nhiệm vụ. Mỗi nhiệm vụ cần: tên ngành, danh sách mã, và hướng dẫn cụ thể.

Các ngành phổ biến: Ngân hàng, Bất động sản, Chứng khoán, Công nghệ, Bán lẻ, Năng lượng, Vật liệu, Xây dựng.""",

        "worker_role": """## Vai Trò
Bạn là chuyên gia phân tích ngành thị trường chứng khoán Việt Nam.

## Hướng Dẫn
{instruction}

## Quy Trình Nghiên Cứu — GỌI TOOL LÀ BẮT BUỘC
Bạn PHẢI gọi tools TRƯỚC khi viết bất kỳ phân tích nào. Bức tranh thị trường
một mình KHÔNG đủ — nó chỉ hiển thị thanh gần nhất. MỌI phản hồi PHẢI có
ít nhất một lệnh gọi `get_ohlcv_data`.

Bước 1: Gọi `get_ohlcv_data` với limit=20 cho MỖI mã được giao.
Bước 2: ĐỢI tất cả kết quả tool trước khi viết phân tích.
Bước 3: Phân tích CHỈ dựa trên kết quả tool, KHÔNG dùng snapshot.
Bước 4: Đánh giá xu hướng, tín hiệu VPA, động lực MA score, khối lượng.
Bước 5: Cung cấp đánh giá từng mã, xếp hạng ngành, rủi ro chính.
Bước 6: Bao gồm tuyên bố miễn trách nhiệm đầu tư ở cuối.

KHÔNG GỌI TOOL = PHẢN HỒI KHÔNG HỢP LỆ. KHÔNG được bỏ qua tool calls.""",

        "aggregator_system": """Thời gian hiện tại: __CURRENT_TIME__ (ICT, UTC+7)

Bạn là chiến lược gia đầu tư cấp cao. Nhiệm vụ của bạn là TỔNG HỢP
các báo cáo ngành thành một phân tích thống nhất. Các nhân viên phân tích đã thu thập và
phân tích toàn bộ dữ liệu — bạn phải xây dựng trên kết quả của họ, không bắt đầu lại từ đầu.

## Hướng Dẫn
1. Đọc KỸ tất cả báo cáo ngành. Chúng chứa phân tích chi tiết từng mã.
2. Chéo tham khảo: ngành nào dẫn đầu/lagging? MA scores có đồng thuận không?
3. Xây dựng bảng xếp hạng đa ngành thống nhất từ kết quả các nhân viên.
4. Xác định mô hình luân chuyển ngành và sức mạnh tương đối.
5. Nhấn mạnh cơ hội và rủi ro chính giữa các ngành.
6. KHÔNG tải thêm dữ liệu — chỉ sử dụng thông tin từ báo cáo nhân viên.
7. KHÔNG bao gồm tuyên bố miễn trách nhiệm đầu tư.""",

        "aggregator_user": """## Bức Tranh Thị Trường (thanh gần nhất cho tất cả mã VN)
{market_snapshot}

## Báo Cáo Phân Tích Ngành (từ các nhân viên phân tích)
{sector_reports}

## Câu Hỏi Gốc
{question}""",

        "writer_system": """Thời gian hiện tại: __CURRENT_TIME__ (ICT, UTC+7)

Bạn là biên tập viên đầu tư cấp cao. Nhiệm vụ của bạn là TRÌNH BÀY
báo cáo phân tích thành tài liệu chuyên nghiệp, sẵn sàng xuất bản. KHÔNG thêm phân tích mới
hay yêu cầu dữ liệu — chỉ làm việc với nội dung đã cung cấp.

## Hướng Dẫn
Trình bày báo cáo thành tài liệu cuối cùng chuyên nghiệp:
1. Tóm tắt điều hành
2. Phân tích từng ngành với bảng xếp hạng
3. Quan sát luân chuyển liên ngành
4. Bảng xếp hạng cổ phiếu đa ngành thống nhất
5. Khuyến nghị chiến lược
6. Bao gồm tuyên bố miễn trách nhiệm đầu tư ở cuối.
Viết rõ ràng, có cấu trúc với tiêu đề, bảng, và gạch đầu dòng.""",

        "writer_user": """## Bức Tranh Thị Trường (thanh gần nhất cho tất cả mã VN)
{market_snapshot}

## Báo Cáo Phân Tích
{analysis}

## Câu Hỏi Gốc
{question}""",
    },
}

_P = _PROMPTS[LANG]


# ── LLM ──

if not settings.openai_api_key:
    raise ValueError(
        "OPENAI_API_KEY is not set. Set it via environment variable or .env file."
    )

llm = ChatOpenAI(
    api_key=settings.openai_api_key,
    base_url=settings.openai_base_url,
    model=settings.openai_model,
)

# ── Helpers ──


def _invoke_with_retry(llm_call, retries: int = 5, base_delay: float = 10.0):
    """Invoke an LLM call with retry on rate-limit errors."""
    for attempt in range(retries):
        try:
            return llm_call()
        except Exception as e:
            if "429" in str(e) and attempt < retries - 1:
                delay = base_delay * (2 ** attempt)
                delay = min(delay, 60)
                print(f"    [retry] Rate limited, waiting {delay:.0f}s (attempt {attempt + 1}/{retries})")
                time.sleep(delay)
            else:
                raise


def _stream_agent(agent, input_msg: str, label: str = "") -> str:
    """Stream an agent and collect all AIMessage content parts."""
    prefix = f"  [{label}] " if label else "    "
    result_parts = []
    for event in agent.stream(
        {"messages": [HumanMessage(content=input_msg)]},
        stream_mode="updates",
    ):
        for _node_name, update in event.items():
            for msg in update.get("messages", []):
                msg_type = type(msg).__name__
                if msg_type == "AIMessage" and getattr(msg, "tool_calls", None):
                    for tc in msg.tool_calls:
                        print(f"{prefix}[tool call] {tc['name']}({tc['args']})")
                elif msg_type == "ToolMessage":
                    preview = msg.content[:150].replace("\n", " ")
                    print(f"{prefix}[tool result] {preview}...")
                elif msg_type == "AIMessage" and msg.content:
                    result_parts.append(msg.content)

    result_text = ""
    for part in result_parts:
        if len(part) > len(result_text):
            result_text = part
    return result_text


def _run_agent_with_tools(system_prompt: str, user_message: str, label: str = "", retries: int = 5) -> str:
    """Run a create_agent with tools, retry on rate-limit, return final text."""
    agent = create_agent(
        llm,
        [get_ticker_list, get_ohlcv_data],
        system_prompt=system_prompt,
    )

    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            return _stream_agent(agent, user_message, label=label)
        except Exception as e:
            last_error = e
            if "429" in str(e) and attempt < retries - 1:
                delay = min(10.0 * (2 ** attempt), 60)
                print(f"  [{label}] [retry] Rate limited, waiting {delay:.0f}s (attempt {attempt + 1}/{retries})")
                time.sleep(delay)
    assert last_error is not None
    raise last_error


# ── Nodes ──


def supervisor_node(state: OverallState) -> dict:
    """Decompose research question into sector subtasks via tool-calling."""
    user_question = ""
    for msg in state["messages"]:
        if isinstance(msg, HumanMessage):
            user_question = msg.content
            break

    supervisor = llm.bind_tools([create_subtasks])

    user_message = (
        f"## Market Snapshot (latest bar for all VN tickers)\n{state['market_snapshot']}\n\n"
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

    subtasks = [Subtask(
        sector=st["sector"],
        tickers=st["tickers"],
        instruction=st["instruction"],
    ) for st in subtasks_data]

    print(f"[Supervisor] Decomposed into {len(subtasks)} subtasks:")
    for st in subtasks:
        print(f"  - {st['sector']}: {', '.join(st['tickers'])}")
    print()

    return {"subtasks": subtasks}


def fan_out(state: OverallState) -> list[Send]:
    """Fan out to parallel worker agents via Send()."""
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
    """Sector worker agent with real tool-calling."""
    sector = state["sector"]
    tickers = state["tickers"]

    print(f"  [Worker:{sector}] Starting analysis for {', '.join(tickers)}...")

    try:
        system_prompt = get_system_prompt(LANG) + "\n\n" + _P["worker_role"].format(
            instruction=state["instruction"],
        )

        result_text = _run_agent_with_tools(
            system_prompt=system_prompt,
            user_message=state["messages"][0].content if state["messages"] else state["instruction"],
            label=f"Worker:{sector}",
        )

        print(f"  [Worker:{sector}] Analysis complete ({len(result_text):,} chars)\n")

        return {"worker_results": [WorkerResult(
            sector=sector, tickers=tickers, analysis=result_text,
        )]}

    except Exception as e:
        print(f"  [Worker:{sector}] ERROR: {e}\n")
        return {"worker_results": [WorkerResult(
            sector=sector, tickers=tickers,
            analysis=f"[Analysis failed for {sector}: {e}]",
        )]}


def aggregator_node(state: OverallState) -> dict:
    """Synthesize worker reports into unified analysis (no tools — pure LLM)."""
    results = state["worker_results"]
    print(f"[Aggregator] Synthesizing {len(results)} sector reports...\n")

    user_question = ""
    for msg in state["messages"]:
        if isinstance(msg, HumanMessage):
            user_question = msg.content
            break

    sector_reports = ""
    for wr in results:
        sector_reports += f"\n### {wr['sector']} ({', '.join(wr['tickers'])})\n\n{wr['analysis']}\n\n"

    system_prompt = (
        get_system_prompt(LANG, include_data_policy=False, include_analysis_framework=True)
        + "\n\n"
        + _P["aggregator_system"].replace("__CURRENT_TIME__", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M ICT"))
    )
    user_message = _P["aggregator_user"].format(
        market_snapshot=state["market_snapshot"],
        sector_reports=sector_reports,
        question=user_question,
    )

    response = _invoke_with_retry(lambda: llm.invoke([
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_message),
    ]))
    content = response.content or ""

    print(f"[Aggregator] Analysis synthesized ({len(content):,} chars)\n")
    return {"analysis": content}


def writer_node(state: OverallState) -> dict:
    """Format the analysis into a final publication-ready report."""
    print(f"[Writer] Formatting final report...\n")

    user_question = ""
    for msg in state["messages"]:
        if isinstance(msg, HumanMessage):
            user_question = msg.content
            break

    system_prompt = (
        get_system_prompt(LANG, include_data_policy=False, include_analysis_framework=False)
        + "\n\n"
        + _P["writer_system"].replace("__CURRENT_TIME__", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M ICT"))
    )
    user_message = _P["writer_user"].format(
        market_snapshot=state["market_snapshot"],
        analysis=state.get("analysis", ""),
        question=user_question,
    )

    response = _invoke_with_retry(lambda: llm.invoke([
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_message),
    ]))
    content = response.content or ""

    print(f"[Writer] Report generated ({len(content):,} chars)\n")
    return {"final_report": content}


# ── Checkpoint callback ──


def extract_worker_results(channel_values: dict[str, Any], session_dir: Path) -> None:
    """Extract per-sector worker analysis and final report to .md files."""
    for wr in channel_values.get("worker_results", []):
        sector = wr.get("sector", "unknown")
        analysis = wr.get("analysis", "")
        safe_name = sector.replace(" ", "_")[:60]
        (session_dir / f"worker_{safe_name}.md").write_text(
            f"# Sector: {sector}\n\n{analysis}\n"
        )
    if channel_values.get("final_report"):
        (session_dir / "final_report.md").write_text(channel_values["final_report"])


# ── Graph ──


def build_graph(checkpointer=None):
    """Build the multi-agent graph with parallel fan-out."""
    graph = StateGraph(OverallState)

    graph.add_node("supervisor", supervisor_node)
    graph.add_node("worker", worker_node)
    graph.add_node("aggregator", aggregator_node)
    graph.add_node("writer", writer_node)

    graph.add_edge(START, "supervisor")
    graph.add_conditional_edges("supervisor", fan_out, ["worker"])
    graph.add_edge("worker", "aggregator")
    graph.add_edge("aggregator", "writer")
    graph.add_edge("writer", END)

    return graph.compile(checkpointer=checkpointer)


# ── Main ──


def main(resume_id: str | None = None):
    started_at = time.time()

    print("# AIPriceAction Multi-Agent Research")
    print()
    print(f"  Model:    {settings.openai_model}")
    print(f"  Base URL: {settings.openai_base_url}")
    print(f"  Started:  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print(f"  Lang:     {LANG}")
    if resume_id:
        print(f"  Resume:   {resume_id}")
    print()
    print("---")
    print()

    if not resume_id:
        print("[1] Fetching market snapshot (all VN tickers, latest bar)...")
        market_snapshot = _builder.build(
            source="vn",
            interval="1D",
            limit=1,
            reference_ticker=None,
            include_system_prompt=False,
        )
        tickers = _client.get_tickers(source="vn")
        print(f"    Tickers: {len(tickers)}\n")

    _QUESTIONS = {
        "en": (
            "Provide a comprehensive market overview of the Vietnamese stock market. "
            "Use the market snapshot to identify the most active sectors and tickers, "
            "then research 2-3 sectors in depth with full OHLCV data. "
            "For each sector: fetch data for all representative tickers plus related tickers, "
            "assess trend direction, VPA signals, MA score momentum across timeframes, "
            "volume confirmation, and identify sector leaders vs laggards. "
            "Then synthesize cross-sector rotation patterns and provide a unified ranking."
        ),
        "vn": (
            "Cung cấp tổng quan thị trường chứng khoán Việt Nam toàn diện. "
            "Sử dụng bức tranh thị trường để xác định ngành và mã hoạt động mạnh nhất, "
            "sau đó nghiên cứu sâu 2-3 ngành với dữ liệu OHLCV đầy đủ. "
            "Mỗi ngành: tải dữ liệu tất cả mã đại diện và các mã liên quan, "
            "đánh giá xu hướng, tín hiệu VPA, động lực MA score qua các khung thời gian, "
            "xác nhận khối lượng, và xác định mã dẫn đầu/lagging trong ngành. "
            "Sau đó tổng hợp mô hình luân chuyển liên ngành và xếp hạng thống nhất."
        ),
    }

    QUESTION = _QUESTIONS[LANG]

    print(f"[2] Starting multi-agent research...\n")
    print(f"    Question: {QUESTION}\n")
    print("---")
    print()

    checkpointer = PersistentCheckpointSaver(
        session_id=resume_id,
        base_dir=Path(tempfile.gettempdir()) / "aipriceaction-checkpoints",
        callbacks=[extract_worker_results],
    )
    print(f"    Session: {checkpointer.session_id}")
    print(f"    Folder:  {checkpointer.session_dir}\n")

    graph = build_graph(checkpointer=checkpointer)

    if resume_id:
        print("    Resuming from checkpoint...\n")
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
                "messages": [HumanMessage(content=QUESTION)],
                "market_snapshot": market_snapshot,
            },
            config={
                "recursion_limit": 50,
                "configurable": {"thread_id": checkpointer.session_id},
            },
        )

    print("---")
    print()
    print("## [3] FINAL REPORT")
    print()
    print("---")
    print()
    print(result["final_report"])
    print()
    print("---")
    print()
    elapsed = time.time() - started_at
    print(f"[4] Done in {elapsed:.1f}s | Checkpoint: {checkpointer.session_dir}")


if __name__ == "__main__":
    import sys
    resume_id = sys.argv[1] if len(sys.argv) > 1 else None
    main(resume_id=resume_id)


if __name__ == "__main__":
    main()
