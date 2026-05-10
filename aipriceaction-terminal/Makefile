.PHONY: run test lint install

run:
	uv run python -m aipriceaction_terminal.cli

test:
	uv run pytest tests/ -v

lint:
	uv run ruff check --fix src/ tests/

install:
	uv tool install --force --python 3.13 -e .
