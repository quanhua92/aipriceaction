from aipriceaction import AIContextBuilder
from settings import settings


def main():
    print(f"OpenAI model: {settings.openai_model}")
    print(f"Anthropic model: {settings.anthropic_model}")
    print(f"Mock only: {settings.mock_only}")
    print()

    builder = AIContextBuilder(lang="en")

    for i, q in enumerate(builder.questions("single")):
        print(f"  [{i}] {q['title']}: {q['snippet']}")

    context = builder.build(
        ticker="VCB", interval="1D", limit=5,
        question=builder.questions("single")[0]["question"],
    )
    print(context)


if __name__ == "__main__":
    main()
