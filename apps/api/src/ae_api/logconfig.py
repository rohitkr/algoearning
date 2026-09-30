"""Structured logging (structlog): key=value in development, JSON lines in staging/production.
Every line carries the request id bound by the middleware, so one request can be followed end to end."""

from __future__ import annotations

import logging

import structlog


def configure_logging(level: str = "INFO", json: bool = False) -> None:
    processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    processors.append(structlog.processors.JSONRenderer() if json else structlog.dev.ConsoleRenderer())
    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelNamesMapping().get(level.upper(), 20)),
        cache_logger_on_first_use=True,
    )


log = structlog.get_logger("ae_api")
