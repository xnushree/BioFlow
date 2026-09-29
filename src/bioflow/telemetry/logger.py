"""Logging configuration for BioFlow-X.

Library modules never configure logging themselves; they only do
``logger = logging.getLogger(__name__)``. Because every module lives under the
``bioflow`` package, all their loggers are children of the ``bioflow`` logger,
and the application entry point (scenario runner, API, tests) calls
``configure_logging`` once to decide where output goes and how verbose it is.

This keeps the dependency direction clean: ``core`` never imports ``telemetry``.
"""

from __future__ import annotations

import logging
from pathlib import Path

from bioflow.core.exceptions import ConfigurationError

ROOT_LOGGER_NAME = "bioflow"
LOG_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"
VALID_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")


def configure_logging(level: str = "INFO", log_file: Path | None = None) -> logging.Logger:
    """Configure the ``bioflow`` logger tree and return its root.

    Safe to call repeatedly: existing handlers are replaced, not duplicated.

    Args:
        level: One of DEBUG, INFO, WARNING, ERROR (case-insensitive).
        log_file: Optional file to write to in addition to the console.

    Raises:
        ConfigurationError: If ``level`` is not a recognised level name.
    """
    level_name = level.upper()
    if level_name not in VALID_LEVELS:
        raise ConfigurationError(
            f"Invalid log level {level!r}; expected one of {', '.join(VALID_LEVELS)}"
        )

    root = logging.getLogger(ROOT_LOGGER_NAME)
    root.setLevel(level_name)
    root.propagate = False  # keep BioFlow-X output independent of other libraries' config

    for handler in list(root.handlers):
        root.removeHandler(handler)
        handler.close()

    formatter = logging.Formatter(LOG_FORMAT)
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    root.addHandler(console)

    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)

    return root
