"""Load every protocol in a directory, keyed by protocol name."""

from __future__ import annotations

from pathlib import Path

from bioflow.core.exceptions import ProtocolError
from bioflow.domain import Protocol
from bioflow.protocols.parser import load_protocol
from bioflow.protocols.validator import ProtocolValidationError

PROTOCOL_SUFFIXES = (".yaml", ".yml")


def load_protocol_library(directory: Path) -> dict[str, Protocol]:
    """Load all ``*.yaml`` / ``*.yml`` protocols in ``directory``.

    Every file is checked before failing, so one run reports all broken files.

    Raises:
        ProtocolError: if the directory is missing, any file is invalid, or
            two files define the same protocol name.
    """
    if not directory.is_dir():
        raise ProtocolError(f"Protocol directory not found: {directory}")

    library: dict[str, Protocol] = {}
    origin: dict[str, str] = {}
    problems: list[str] = []
    for path in sorted(p for p in directory.iterdir() if p.suffix in PROTOCOL_SUFFIXES):
        try:
            protocol = load_protocol(path)
        except ProtocolValidationError as error:
            problems.append(str(error))
            continue
        if protocol.name in library:
            problems.append(f"{path.name}: protocol name '{protocol.name}' already defined in {origin[protocol.name]}")
            continue
        library[protocol.name] = protocol
        origin[protocol.name] = path.name

    if problems:
        raise ProtocolError("Invalid protocol library:\n" + "\n".join(problems))
    return library
