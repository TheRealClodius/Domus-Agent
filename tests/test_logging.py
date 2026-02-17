"""Tests for agent/logging.py — structured JSON logging."""

import json
import logging

from agent.logging import get_logger, log_tool_execution, setup_logging


class TestJsonOutput:
    """Log output should be valid JSON with standard fields."""

    def test_log_output_is_valid_json(self, capfd):
        setup_logging()
        logger = get_logger("test.json_output")
        logger.info("hello world")

        captured = capfd.readouterr()
        line = captured.err.strip().split("\n")[-1]
        record = json.loads(line)

        assert record["message"] == "hello world"
        assert record["level"] == "INFO"
        assert "timestamp" in record
        assert record["logger"] == "test.json_output"

    def test_extra_fields_included_in_json(self, capfd):
        setup_logging()
        logger = get_logger("test.extra")
        logger.info("with extras", extra={"space_id": "sp_1", "user_id": "u_1"})

        captured = capfd.readouterr()
        line = captured.err.strip().split("\n")[-1]
        record = json.loads(line)

        assert record["space_id"] == "sp_1"
        assert record["user_id"] == "u_1"


class TestCorrelationFields:
    """Logs should carry space_id and user_id when provided."""

    def test_correlation_fields_present(self, capfd):
        setup_logging()
        logger = get_logger("test.correlation")
        logger.info(
            "processing request",
            extra={"space_id": "space_abc", "user_id": "user_42"},
        )

        captured = capfd.readouterr()
        line = captured.err.strip().split("\n")[-1]
        record = json.loads(line)

        assert record["space_id"] == "space_abc"
        assert record["user_id"] == "user_42"

    def test_missing_correlation_fields_not_in_output(self, capfd):
        setup_logging()
        logger = get_logger("test.no_correlation")
        logger.info("no context")

        captured = capfd.readouterr()
        line = captured.err.strip().split("\n")[-1]
        record = json.loads(line)

        assert "space_id" not in record
        assert "user_id" not in record


class TestToolExecutionLogging:
    """log_tool_execution should produce a structured log entry with tool name and duration."""

    def test_tool_execution_logged(self, capfd):
        setup_logging()
        logger = get_logger("test.tool_exec")
        log_tool_execution(
            logger,
            tool_name="create_entity",
            duration_ms=42.5,
            space_id="sp_1",
            user_id="u_1",
        )

        captured = capfd.readouterr()
        line = captured.err.strip().split("\n")[-1]
        record = json.loads(line)

        assert record["message"] == "tool_execution"
        assert record["tool_name"] == "create_entity"
        assert record["duration_ms"] == 42.5
        assert record["space_id"] == "sp_1"
        assert record["user_id"] == "u_1"
        assert record["level"] == "INFO"

    def test_tool_execution_without_correlation(self, capfd):
        setup_logging()
        logger = get_logger("test.tool_exec_no_ctx")
        log_tool_execution(
            logger,
            tool_name="query_entities",
            duration_ms=100.0,
        )

        captured = capfd.readouterr()
        line = captured.err.strip().split("\n")[-1]
        record = json.loads(line)

        assert record["tool_name"] == "query_entities"
        assert record["duration_ms"] == 100.0
        assert "space_id" not in record
        assert "user_id" not in record


class TestLoggingActivation:
    """setup_logging() must be called at app startup (main.py)."""

    def test_main_module_calls_setup_logging(self):
        """main.py should import and call setup_logging() at module level."""
        import main  # noqa: F401

        # If setup_logging has been called, root logger has a handler with _JsonFormatter
        root = logging.getLogger()
        json_handlers = [
            h for h in root.handlers
            if hasattr(h, "formatter") and type(h.formatter).__name__ == "_JsonFormatter"
        ]
        assert len(json_handlers) >= 1, (
            "setup_logging() was not called — no _JsonFormatter handler on root logger"
        )
