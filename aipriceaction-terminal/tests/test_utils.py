"""Tests for utils.py display helpers."""

from unittest.mock import MagicMock

from aipriceaction_terminal.utils import write_context_result, write_error


class TestWriteContextResult:
    def test_write_context_result_basic(self):
        log = MagicMock()
        context = "AAA\nBBB\nCCC"
        write_context_result(log, "VCB", "1D", context)

        calls = log.write.call_args_list
        assert len(calls) == 3  # header + content + blank line
        header = calls[0][0][0]
        assert "VCB" in header
        assert "1D" in header
        assert "11 chars" in header
        assert "3 lines" in header

    def test_write_context_result_many_lines_no_truncation(self):
        log = MagicMock()
        lines = "\n".join(f"L{i}" for i in range(50))
        write_context_result(log, "VIC", "1W", lines)

        calls = log.write.call_args_list
        # header + full content + blank line (no truncation)
        assert len(calls) == 3
        assert all("truncated" not in c[0][0] for c in calls)
        content_call = calls[1][0][0]
        assert "L49" in content_call

    def test_write_context_result_empty_string(self):
        log = MagicMock()
        write_context_result(log, "AAA", "1D", "")

        calls = log.write.call_args_list
        header = calls[0][0][0]
        assert "0 chars" in header
        assert "1 lines" in header
        # No truncation for empty content
        assert len(calls) == 3

    def test_write_context_result_single_line(self):
        log = MagicMock()
        write_context_result(log, "VNM", "1D", "only one line")

        calls = log.write.call_args_list
        header = calls[0][0][0]
        assert "13 chars" in header
        assert "1 lines" in header
        assert len(calls) == 3  # no truncation

    def test_write_error_basic(self):
        log = MagicMock()
        write_error(log, RuntimeError("something broke"))

        calls = log.write.call_args_list
        assert len(calls) == 1
        assert "[bold red]Error:[/bold red]" in calls[0][0][0]
        assert "something broke" in calls[0][0][0]

    def test_write_error_exception_type(self):
        log = MagicMock()
        write_error(log, ValueError("invalid value"))

        msg = log.write.call_args[0][0]
        assert "invalid value" in msg

    def test_write_error_network_error(self):
        log = MagicMock()
        write_error(log, ConnectionError("connection refused"))

        msg = log.write.call_args[0][0]
        assert "connection refused" in msg
