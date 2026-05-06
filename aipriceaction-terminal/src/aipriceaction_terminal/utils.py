"""Shared display helpers for RichLog output formatting."""

from textual.widgets import RichLog


def write_context_result(
    log: RichLog, ticker: str, interval: str, context: str
) -> None:
    """Write a formatted context build result to a RichLog."""
    lines = context.split("\n")
    log.write(
        f"[bold green]Context built[/bold green] for [bold]{ticker}[/bold] "
        f"({interval}) - {len(context):,} chars, {len(lines)} lines\n"
    )
    log.write("[dim]" + context + "[/dim]")
    log.write("")


def write_error(log: RichLog, error: Exception) -> None:
    """Write a formatted error to a RichLog."""
    log.write(f"[bold red]Error:[/bold red] {error}")
