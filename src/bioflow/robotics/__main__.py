"""Render a laboratory layout as text.

Usage:
    python -m bioflow.robotics configs/laboratory.yaml
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from bioflow.core.exceptions import ConfigurationError
from bioflow.robotics.layout import load_layout
from bioflow.robotics.map import LEGEND


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m bioflow.robotics", description="Render a lab layout.")
    parser.add_argument("layout", type=Path)
    args = parser.parse_args(argv)
    try:
        layout = load_layout(args.layout)
    except ConfigurationError as error:
        print(error, file=sys.stderr)
        return 1
    lab_map = layout.map
    size_m = (lab_map.width * lab_map.cell_size_m, lab_map.height * lab_map.cell_size_m)
    print(f"{lab_map.width}x{lab_map.height} cells ({size_m[0]:g} m x {size_m[1]:g} m), "
          f"{len(lab_map.equipment_ids)} equipment positions, robot speed {layout.robot_speed_m_per_min:g} m/min\n")
    print(lab_map.render())
    print(f"\n{LEGEND}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
