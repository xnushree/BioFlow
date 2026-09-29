"""Command-line protocol checker.

Usage:
    python -m bioflow.protocols protocols/                 # check a directory
    python -m bioflow.protocols protocols/basic_experiment.yaml
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from bioflow.core.exceptions import ProtocolError
from bioflow.protocols.library import load_protocol_library
from bioflow.protocols.parser import load_protocol


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m bioflow.protocols", description="Validate protocol files.")
    parser.add_argument("path", type=Path, help="a protocol file or a directory of protocol files")
    args = parser.parse_args(argv)

    try:
        if args.path.is_dir():
            protocols = list(load_protocol_library(args.path).values())
        else:
            protocols = [load_protocol(args.path)]
    except ProtocolError as error:
        print(error, file=sys.stderr)
        return 1

    for protocol in protocols:
        steps = " -> ".join(
            f"{s.operation}({s.duration_min:g} min)" if s.duration_min else str(s.operation)
            for s in protocol.steps
        )
        print(f"OK  {protocol.name} [{protocol.cell_type}]: {steps}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
