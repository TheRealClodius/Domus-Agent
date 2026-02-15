"""Structured JSON logging for the Domus Agent service.

Configures Python stdlib logging to emit JSON lines to stderr.
No external logging libraries — just logging + json.
"""

import json
import logging
import sys
from datetime import datetime, timezone


class _JsonFormatter(logging.Formatter):
    """Formats log records as single-line JSON objects."""

    # Fields from LogRecord that we never want in output.
    _SKIP_FIELDS = frozenset({
        "args", "created", "exc_info", "exc_text", "filename", "funcName",
        "levelname", "levelno", "lineno", "module", "msecs", "msg", "name",
        "pathname", "process", "processName", "relativeCreated", "stack_info",
        "thread", "threadName", "taskName",
    })

    def format(self, record: logging.LogRecord) -> str:
        entry: dict = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "message": record.getMessage(),
            "logger": record.name,
        }

        # Merge any extra fields the caller passed.
        for key, value in record.__dict__.items():
            if key.startswith("_") or key in self._SKIP_FIELDS:
                continue
            if key in entry:
                continue
            entry[key] = value

        if record.exc_info and record.exc_info[0] is not None:
            entry["exception"] = self.formatException(record.exc_info)

        return json.dumps(entry, default=str)


def setup_logging(level: int = logging.INFO) -> None:
    """Configure the root logger with JSON output to stderr.

    Safe to call multiple times — clears existing handlers first.
    """
    root = logging.getLogger()
    root.setLevel(level)

    # Remove any existing handlers to avoid duplicates on repeated calls.
    root.handlers.clear()

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(_JsonFormatter())
    root.addHandler(handler)


def get_logger(name: str) -> logging.Logger:
    """Return a named logger instance."""
    return logging.getLogger(name)


def log_tool_execution(
    logger: logging.Logger,
    tool_name: str,
    duration_ms: float,
    space_id: str | None = None,
    user_id: str | None = None,
) -> None:
    """Emit a structured log entry for a tool execution."""
    extra: dict = {"tool_name": tool_name, "duration_ms": duration_ms}
    if space_id is not None:
        extra["space_id"] = space_id
    if user_id is not None:
        extra["user_id"] = user_id
    logger.info("tool_execution", extra=extra)
