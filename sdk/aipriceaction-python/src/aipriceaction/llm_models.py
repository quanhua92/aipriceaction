from dataclasses import dataclass


@dataclass(frozen=True)
class Model:
    """An LLM model available on OpenRouter."""

    id: str
    label: str


class OpenRouter:
    """Curated OpenRouter models for AIContextBuilder.

    Usage:
        from aipriceaction.llm_models import OpenRouter

        settings.openai_model = OpenRouter.LIQUID_LFM_2_5_1_2B_THINKING_FREE.id
    """

    # Google
    GOOGLE_GEMMA_4_31B_IT_FREE = Model(
        "google/gemma-4-31b-it:free",
        "Google Gemma 4 31B IT (Free)",
    )

    # Liquid
    LIQUID_LFM_2_5_1_2B_THINKING_FREE = Model(
        "liquid/lfm-2.5-1.2b-thinking:free",
        "Liquid LFM 2.5 1.2B Thinking (Free)",
    )

    # MiniMax
    MINIMAX_MINIMAX_M2_5_FREE = Model(
        "minimax/minimax-m2.5:free",
        "MiniMax MiniMax M2.5 (Free)",
    )

    # NVIDIA
    NVIDIA_NEMOTRON_NANO_12B_V2_VL_FREE = Model(
        "nvidia/nemotron-nano-12b-v2-vl:free",
        "NVIDIA Nemotron Nano 12B V2 VL (Free)",
    )

    NVIDIA_NEMOTRON_3_NANO_OMNI_30B_A3B_REASONING_FREE = Model(
        "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
        "NVIDIA Nemotron 3 Nano Omni 30B A3B Reasoning (Free)",
    )

    NVIDIA_NEMOTRON_3_SUPER_120B_A12B_FREE = Model(
        "nvidia/nemotron-3-super-120b-a12b:free",
        "NVIDIA Nemotron 3 Super 120B A12B (Free)",
    )

    # OpenAI
    OPENAI_GPT_OSS_20B_FREE = Model(
        "openai/gpt-oss-20b:free",
        "OpenAI GPT OSS 20B (Free)",
    )

    OPENAI_GPT_OSS_120B_FREE = Model(
        "openai/gpt-oss-120b:free",
        "OpenAI GPT OSS 120B (Free)",
    )

    # Poolside
    POOLSIDE_LAGUNA_XS_2_FREE = Model(
        "poolside/laguna-xs.2:free",
        "Poolside Laguna XS.2 (Free)",
    )

    # Qwen
    QWEN_QWEN3_CODER_FREE = Model(
        "qwen/qwen3-coder:free",
        "Qwen Qwen3 Coder (Free)",
    )

    QWEN_QWEN3_NEXT_80B_A3B_INSTRUCT_FREE = Model(
        "qwen/qwen3-next-80b-a3b-instruct:free",
        "Qwen Qwen3 Next 80B A3B Instruct (Free)",
    )

    # Z-AI
    Z_AI_GLM_4_5_AIR_FREE = Model(
        "z-ai/glm-4.5-air:free",
        "Z-AI GLM 4.5 Air (Free)",
    )

    FREE: list[Model] = []  # populated after class definition


OpenRouter.FREE = [
    OpenRouter.GOOGLE_GEMMA_4_31B_IT_FREE,
    OpenRouter.LIQUID_LFM_2_5_1_2B_THINKING_FREE,
    OpenRouter.MINIMAX_MINIMAX_M2_5_FREE,
    OpenRouter.NVIDIA_NEMOTRON_NANO_12B_V2_VL_FREE,
    OpenRouter.NVIDIA_NEMOTRON_3_NANO_OMNI_30B_A3B_REASONING_FREE,
    OpenRouter.NVIDIA_NEMOTRON_3_SUPER_120B_A12B_FREE,
    OpenRouter.OPENAI_GPT_OSS_20B_FREE,
    OpenRouter.OPENAI_GPT_OSS_120B_FREE,
    OpenRouter.POOLSIDE_LAGUNA_XS_2_FREE,
    OpenRouter.QWEN_QWEN3_CODER_FREE,
    OpenRouter.QWEN_QWEN3_NEXT_80B_A3B_INSTRUCT_FREE,
    OpenRouter.Z_AI_GLM_4_5_AIR_FREE,
]
