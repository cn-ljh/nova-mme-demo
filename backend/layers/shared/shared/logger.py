"""Structured logging utilities for CloudWatch."""
import json
import logging
import os
import traceback
from typing import Any


def get_logger(name: str) -> logging.Logger:
    """Get a configured logger that outputs structured JSON to CloudWatch."""
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(_StructuredFormatter())
        logger.addHandler(handler)
    logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))
    logger.propagate = False
    return logger


class _StructuredFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        log_entry: dict[str, Any] = {
            "timestamp": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "service": os.environ.get("POWERTOOLS_SERVICE_NAME", "multimodal-retrieval"),
            "stage": os.environ.get("STAGE", "dev"),
        }
        # Include context fields attached to the record
        for field in ("request_id", "user_id", "task_id", "content_id", "function_name"):
            value = getattr(record, field, None)
            if value is not None:
                log_entry[field] = value

        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)
            log_entry["traceback"] = traceback.format_exception(*record.exc_info)

        if hasattr(record, "extra"):
            log_entry.update(record.extra)

        return json.dumps(log_entry, default=str)


class LogContext:
    """Context manager to attach fields to all log records within its scope."""

    def __init__(self, logger: logging.Logger, **fields):
        self.logger = logger
        self.fields = fields
        self._filter = None

    def __enter__(self):
        self._filter = _ContextFilter(self.fields)
        self.logger.addFilter(self._filter)
        return self

    def __exit__(self, *_):
        if self._filter:
            self.logger.removeFilter(self._filter)


class _ContextFilter(logging.Filter):
    def __init__(self, fields: dict):
        super().__init__()
        self.fields = fields

    def filter(self, record: logging.LogRecord) -> bool:
        for key, value in self.fields.items():
            setattr(record, key, value)
        return True
