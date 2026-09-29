"""Tests for loading protocol files, protocol libraries, and the command-line checker."""

from pathlib import Path

import pytest

from bioflow.core.exceptions import ProtocolError
from bioflow.protocols import ProtocolValidationError, load_protocol, load_protocol_library, parse_protocol
from bioflow.protocols.__main__ import main

REPO_PROTOCOLS = Path(__file__).parents[3] / "protocols"

MINIMAL = """\
protocol: {name}
cell_type: HEK293
steps:
  - operation: IMAGE
  - operation: ARCHIVE
"""


def write(directory: Path, filename: str, text: str) -> Path:
    path = directory / filename
    path.write_text(text, encoding="utf-8")
    return path


def test_repository_protocols_are_valid() -> None:
    library = load_protocol_library(REPO_PROTOCOLS)

    assert set(library) == {"basic_experiment", "imaging_experiment", "stress_test"}
    assert library["basic_experiment"].steps[0].duration_min == 720


def test_load_single_file(tmp_path: Path) -> None:
    protocol = load_protocol(write(tmp_path, "p.yaml", MINIMAL.format(name="mini")))

    assert protocol.name == "mini"


def test_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ProtocolValidationError, match="file: not found"):
        load_protocol(tmp_path / "nope.yaml")


def test_yaml_syntax_error_reports_line(tmp_path: Path) -> None:
    path = write(tmp_path, "bad.yaml", "protocol: x\nsteps:\n  - operation: [IMAGE\n")

    with pytest.raises(ProtocolValidationError) as error:
        load_protocol(path)

    assert error.value.source == "bad.yaml"
    assert error.value.issues[0].location.startswith("line ")


def test_error_names_the_file(tmp_path: Path) -> None:
    path = write(tmp_path, "typo.yaml", MINIMAL.format(name="t").replace("cell_type", "cell_typ"))

    with pytest.raises(ProtocolValidationError, match=r"typo\.yaml: 2 problem\(s\)"):
        load_protocol(path)


def test_parse_protocol_raises_with_all_issues() -> None:
    with pytest.raises(ProtocolValidationError) as error:
        parse_protocol({"steps": []}, source="api-request")

    assert error.value.source == "api-request"
    assert len(error.value.issues) >= 3  # protocol, cell_type, steps


def test_library_rejects_duplicate_names(tmp_path: Path) -> None:
    write(tmp_path, "a.yaml", MINIMAL.format(name="same"))
    write(tmp_path, "b.yml", MINIMAL.format(name="same"))

    with pytest.raises(ProtocolError, match="'same' already defined in a.yaml"):
        load_protocol_library(tmp_path)


def test_library_reports_every_broken_file(tmp_path: Path) -> None:
    write(tmp_path, "good.yaml", MINIMAL.format(name="good"))
    write(tmp_path, "bad1.yaml", "protocol: b1\n")
    write(tmp_path, "bad2.yaml", "protocol: b2\n")
    write(tmp_path, "notes.txt", "ignored: not a protocol file")

    with pytest.raises(ProtocolError) as error:
        load_protocol_library(tmp_path)

    assert "bad1.yaml" in str(error.value) and "bad2.yaml" in str(error.value)


def test_library_directory_must_exist(tmp_path: Path) -> None:
    with pytest.raises(ProtocolError, match="not found"):
        load_protocol_library(tmp_path / "missing")


def test_cli_accepts_valid_directory(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([str(REPO_PROTOCOLS)]) == 0
    assert "OK  basic_experiment [HEK293]: INCUBATE(720 min) -> MEDIA_EXCHANGE" in capsys.readouterr().out


def test_cli_reports_invalid_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = write(tmp_path, "bad.yaml", "protocol: x\ncell_type: y\nsteps: []\n")

    assert main([str(path)]) == 1
    assert "steps: expected a non-empty list" in capsys.readouterr().err
