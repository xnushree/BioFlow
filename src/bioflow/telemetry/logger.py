"""Logging configuration for BioFlow-X.

Library modules never configure logging themselves; they only do
``logger = logging.getLogger(__name__)``. Because every module lives under the
``bioflow`` package, all their loggers are children of the ``bioflow`` logger,
and the application entry point (scenario runner, API, tests) calls
``configure_logging`` once to decide where output goes and how verbose it is.

This keeps the dependency direction clean: ``core`` never imports ``telemetry``.

Two formats:
    text  ``2026-01-01 12:00:00 | INFO | bioflow.x | t=720.0 | message``
    json  one JSON object per line (for log shippers and analysis scripts)

If a simulation clock is given, every record carries the simulation time.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from bioflow.core.clock import Clock
from bioflow.core.exceptions import ConfigurationError

ROOT_LOGGER_NAME = "bioflow"
TEXT_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)s | t=%(sim_time)s | %(message)s"
VALID_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")
VALID_FORMATS = ("text", "json")


class _SimTimeFilter(logging.Filter):
    """Attach the current simulation time (or '-') to every record."""

    def __init__(self, clock: Clock | None) -> None:
        super().__init__()
        self.clock = clock

    def filter(self, record: logging.LogRecord) -> bool:
        record.sim_time = f"{self.clock.now:.3f}" if self.clock is not None else "-"
        return True


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "time": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "sim_time": getattr(record, "sim_time", "-"),
            "message": record.getMessage(),
        }
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry)


def configure_logging(
    level: str = "INFO",
    log_file: Path | None = None,
    fmt: str = "text",
    clock: Clock | None = None,
) -> logging.Logger:
    """Configure the ``bioflow`` logger tree and return its root.

    Safe to call repeatedly: existing handlers are replaced, not duplicated.

    Args:
        level: One of DEBUG, INFO, WARNING, ERROR (case-insensitive).
        log_file: Optional file to write to in addition to the console.
        fmt: "text" (human-readable) or "json" (one object per line).
        clock: Simulation clock whose time is stamped on every record.

    Raises:
        ConfigurationError: If ``level`` or ``fmt`` is not recognised.
    """
    level_name = level.upper()
    if level_name not in VALID_LEVELS:
        raise ConfigurationError(
            f"Invalid log level {level!r}; expected one of {', '.join(VALID_LEVELS)}"
        )
    if fmt not in VALID_FORMATS:
        raise ConfigurationError(f"Invalid log format {fmt!r}; expected one of {', '.join(VALID_FORMATS)}")

    root = logging.getLogger(ROOT_LOGGER_NAME)
    root.setLevel(level_name)
    root.propagate = False  # keep BioFlow-X output independent of other libraries' config

    for handler in list(root.handlers):
        root.removeHandler(handler)
        handler.close()

    formatter: logging.Formatter = _JsonFormatter() if fmt == "json" else logging.Formatter(TEXT_FORMAT)
    sim_time = _SimTimeFilter(clock)
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))
    for handler in handlers:
        handler.setFormatter(formatter)
        handler.addFilter(sim_time)
        root.addHandler(handler)

    return root


def attach_clock(clock: Clock) -> None:
    """Stamp simulation time from ``clock`` on all bioflow log records from now on."""
    for handler in logging.getLogger(ROOT_LOGGER_NAME).handlers:
        for log_filter in handler.filters:
            if isinstance(log_filter, _SimTimeFilter):
                log_filter.clock = clock
