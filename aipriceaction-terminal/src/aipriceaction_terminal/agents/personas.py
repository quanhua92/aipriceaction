"""Persona definitions and registry for multi-agent support."""

from __future__ import annotations

from dataclasses import dataclass

from aipriceaction.system import get_system_prompt


@dataclass
class Persona:
    """An agent persona with custom instructions."""

    id: str
    name: str
    description: str
    extra_instructions: str = ""
    include_data_policy: bool = True
    include_analysis_framework: bool = True
    include_ma_score: bool = True
    include_disclaimer: bool = True

    def build_system_prompt(self, lang: str) -> str:
        """Build the full system prompt for this persona."""
        prompt = get_system_prompt(
            lang,
            include_data_policy=self.include_data_policy,
            include_analysis_framework=self.include_analysis_framework,
            include_ma_score=self.include_ma_score,
            include_disclaimer=self.include_disclaimer,
            include_persona=True,
        )
        if self.extra_instructions:
            prompt += "\n\n" + self.extra_instructions
        return prompt


# -- Bilingual extra instructions --

_GENERAL_INSTRUCTIONS = {
    "en": """## Tool Usage

You have tools to fetch market data:
- `get_live_data`: Fetch the latest candle for one or more tickers at once (e.g. "VCB,FPT,BTCUSDT"). Best for a quick market overview.
- `get_ohlcv_data`: Fetch historical OHLCV data with MA indicators for a single ticker. Use when you need multiple candles / deeper context.
- `get_ticker_list`: Discover available tickers grouped by sector/industry.

When the user asks about a specific ticker, market sector, or price-related question, you MUST call the relevant tools before answering. Do not answer from memory alone — always fetch fresh data.

For non-market questions (greetings, general knowledge, etc.), respond naturally without calling tools.

## Research Workflow (when analyzing tickers)
1. **Prefer starting with `get_live_data(tickers="")`** — returns the top 50 tickers sorted by trading value (close * volume). Use the results to spot notable movers, big changes, and strong/weak MA scores before drilling deeper.
2. Then call `get_ohlcv_data` for each important ticker identified in step 1 to get historical context.
3. If relevant, call `get_ticker_list` to understand the sector composition of tickers.
4. Base your analysis ONLY on the tool results.
5. Assess: trend direction, VPA signals, MA score momentum, volume confirmation.
6. Structure your answer clearly with specific data points.
7. Include the investment disclaimer at the end of any financial analysis.""",
    "vn": """## Sử Dụng Công Cụ

Bạn có các công cụ để lấy dữ liệu thị trường:
- `get_live_data`: Lấy nến gần nhất cho một hoặc nhiều mã cùng lúc (VD: "VCB,FPT,BTCUSDT"). Tốt nhất để xem tổng quan thị trường nhanh.
- `get_ohlcv_data`: Lấy dữ liệu OHLCV lịch sử với chỉ báo MA cho một mã. Dùng khi cần nhiều nến / ngữ cảnh sâu hơn.
- `get_ticker_list`: Khám phá các mã chứng khoán theo nhóm ngành.

Khi người dùng hỏi về mã cụ thể, ngành, hoặc câu hỏi liên quan giá — bạn PHẢI gọi tools trước khi trả lời. Không trả lời từ trí nhớ — luôn lấy dữ liệu mới nhất.

Với câu hỏi ngoài thị trường (chào hỏi, kiến thức chung), trả lời tự nhiên không cần gọi tools.

## Quy Trình Nghiên Cứu (khi phân tích mã)
1. **Nên bắt đầu với `get_live_data(tickers="")`** — trả về top 50 mã theo giá trị giao dịch (close * volume). Dùng kết quả để nhận diện mã đáng chú ý trước khi phân tích sâu hơn.
2. Sau đó gọi `get_ohlcv_data` cho mỗi mã quan trọng đã nhận diện ở bước 1 để có ngữ cảnh lịch sử.
3. Nếu cần, gọi `get_ticker_list` để hiểu cấu trúc ngành của các mã.
4. Phân tích CHỈ dựa trên kết quả tools.
5. Đánh giá: xu hướng, tín hiệu VPA, động lực MA score, khối lượng.
6. Cấu trúc câu trả lời rõ ràng với số liệu cụ thể.
7. Bao gồm tuyên bố miễn trách nhiệm đầu tư cuối phân tích.""",
}

_ANALYST_INSTRUCTIONS = {
    "en": """## Tool Usage

You have tools to fetch market data:
- `get_live_data`: Fetch the latest candle for one or more tickers at once. Best for broad market overview.
- `get_ohlcv_data`: Fetch historical OHLCV data with MA indicators for a single ticker. Use for deeper analysis.
- `get_ticker_list`: Discover available tickers grouped by sector/industry.

## Research Workflow (MANDATORY)
1. **Prefer calling `get_live_data(tickers="")` first** — returns the top 50 tickers sorted by trading value (close * volume). Use it to identify notable movers, strong/weak MA scores, and sector patterns before drilling deeper.
2. Call `get_ticker_list` to understand the sector composition and discover related tickers.
3. Call `get_ohlcv_data` for EACH ticker explicitly mentioned by the user AND at least 2-3 additional tickers per sector identified in steps 1-2. Do NOT skip this step.
4. For each ticker, assess: trend direction, VPA signals (accumulation/distribution), MA score momentum across timeframes, volume confirmation, and support/resistance.
5. Structure your final answer with:
   - Per-ticker analysis with specific data points from the tool results
   - Sector rotation observations (which sectors are leading/lagging)
   - Multi-ticker ranking table
6. Include the investment disclaimer at the end.

FAILURE TO CALL TOOLS = INVALID RESPONSE.""",
    "vn": """## Sử Dụng Công Cụ

Bạn có các công cụ để lấy dữ liệu thị trường:
- `get_live_data`: Lấy nến gần nhất cho một hoặc nhiều mã cùng lúc. Tốt nhất để xem tổng quan thị trường.
- `get_ohlcv_data`: Lấy dữ liệu OHLCV lịch sử với chỉ báo MA cho một mã. Dùng cho phân tích sâu hơn.
- `get_ticker_list`: Khám phá các mã chứng khoán theo nhóm ngành.

## Quy Trình Nghiên Cứu (BẮT BUỘC)
1. **Nên gọi `get_live_data(tickers="")` đầu tiên** — trả về top 50 mã theo giá trị giao dịch (close * volume). Dùng nó để nhận diện mã đáng chú ý, MA score cao/thấp, và pattern ngành trước khi phân tích sâu hơn.
2. Gọi `get_ticker_list` để hiểu cấu trúc ngành và tìm mã liên quan.
3. Gọi `get_ohlcv_data` cho MỖI mã được nhắc đến VÀ ít nhất 2-3 mã thêm mỗi ngành đã nhận diện ở bước 1-2. KHÔNG được bỏ qua bước này.
4. Mỗi mã: đánh giá xu hướng, tín hiệu VPA, động lực MA score, khối lượng, hỗ trợ/kháng cự.
5. Cấu trúc câu trả lời:
   - Phân tích từng mã với số liệu cụ thể từ tools
   - Quan sát luân chuyển ngành (ngành dẫn đầu/lagging)
   - Bảng xếp hạng đa mã
6. Bao gồm tuyên bố miễn trách nhiệm đầu tư ở cuối.

KHÔNG GỌI TOOL = PHẢN HỒI KHÔNG HỢP LỆ.""",
}


def _bilingual(texts: dict[str, str], lang: str) -> str:
    return texts.get(lang, texts["en"])


class PersonaRegistry:
    """Registry for agent personas."""

    def __init__(self) -> None:
        self._personas: dict[str, Persona] = {}
        self._default_id: str = ""

    def register(self, persona: Persona, *, is_default: bool = False) -> None:
        self._personas[persona.id] = persona
        if is_default or not self._default_id:
            self._default_id = persona.id

    def get(self, persona_id: str) -> Persona | None:
        return self._personas.get(persona_id)

    def list_personas(self) -> list[Persona]:
        return list(self._personas.values())

    @property
    def default_id(self) -> str:
        return self._default_id


def get_default_personas(lang: str = "en") -> PersonaRegistry:
    """Return a PersonaRegistry with the built-in personas."""
    registry = PersonaRegistry()

    registry.register(
        Persona(
            id="general",
            name="General Advisor",
            description="Handles both market and non-market questions. Auto-calls tools when needed.",
            extra_instructions=_bilingual(_GENERAL_INSTRUCTIONS, lang),
        ),
        is_default=True,
    )

    registry.register(
        Persona(
            id="analyst",
            name="Deep Analyst",
            description="Deep multi-ticker specialist with mandatory research workflow.",
            extra_instructions=_bilingual(_ANALYST_INSTRUCTIONS, lang),
        ),
    )

    return registry


def get_default_persona(lang: str = "en") -> Persona:
    """Return the default (general) persona."""
    registry = get_default_personas(lang)
    persona = registry.get(registry.default_id)
    assert persona is not None
    return persona
