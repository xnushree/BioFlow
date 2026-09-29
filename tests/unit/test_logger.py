"""Tests for logging configuration."""

import logging
from collections.abc import Iterator
from pathlib import Path

import pytest

from bioflow.core.exceptions import ConfigurationError
from bioflow.telemetry.logger import ROOT_LOGGER_NAME, configure_logging


@pytest.fixture(autouse=True)
def reset_bioflow_logger() -> Iterator[None]:
    """Remove handlers after each test so tests do not leak logging state."""
    yield
    root = logging.getLogger(ROOT_LOGGER_NAME)
    for handler in list(root.handlers):
        root.removeHandler(handler)
        handler.close()


def test_sets_requested_level_case_insensitively() -> None:
    root = configure_logging("debug")

    assert root.level == logging.DEBUG


def test_rejects_unknown_level() -> None:
    with pytest.raises(ConfigurationError, match="Invalid log level"):
        configure_logging("LOUD")


def test_repeated_calls_do_not_duplicate_handlers() -> None:
    configure_logging("INFO")
    root = configure_logging("INFO")

    assert len(root.handlers) == 1


def test_child_module_loggers_inherit_configuration(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("WARNING")
    child = logging.getLogger("bioflow.equipment.robot")

    child.info("hidden")
    child.warning("robot timeout")

    output = capsys.readouterr().err
    assert "robot timeout" in output
    assert "hidden" not in output
    assert "bioflow.equipment.robot" in output


def test_writes_to_log_file_when_requested(tmp_path: Path) -> None:
    log_file = tmp_path / "logs" / "run.log"
    configure_logging("INFO", log_file=log_file)

    logging.getLogger("bioflow.core").info("simulation started")
    for handler in logging.getLogger(ROOT_LOGGER_NAME).handlers:
        handler.flush()

    assert "simulation started" in log_file.read_text(encoding="utf-8")
