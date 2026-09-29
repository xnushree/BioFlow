"""Render a laboratory layout as text, optionally with a planned robot route.

Usage:
    python -m bioflow.robotics configs/laboratory.yaml
    python -m bioflow.robotics configs/laboratory.yaml --route STORAGE_01 IMAGING_02
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from bioflow.core.exceptions import BioFlowError
from bioflow.robotics.layout import load_layout
from bioflow.robotics.map import LEGEND, Cell
from bioflow.robotics.travel import MapTravelTime


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m bioflow.robotics", description="Render a lab layout.")
    parser.add_argument("layout", type=Path)
    parser.add_argument("--route", nargs=2, metavar=("FROM", "TO"), help="equipment IDs to plan a route between")
    args = parser.parse_args(argv)

    marks: dict[Cell, str] = {}
    route_line = ""
    try:
        layout = load_layout(args.layout)
        if args.route:
            travel = MapTravelTime(layout.map, layout.robot_speed_m_per_min)
            path = travel.path(*args.route)
            marks = {cell: "*" for cell in path.cells[1:-1]}
            route_line = (
                f"\nRoute {args.route[0]} -> {args.route[1]}: {path.steps} steps, cost {path.cost:g}, "
                f"{travel.travel_time(*args.route):.2f} min   (* = path)"
            )
    except BioFlowError as error:
        print(error, file=sys.stderr)
        return 1

    lab_map = layout.map
    size_m = (lab_map.width * lab_map.cell_size_m, lab_map.height * lab_map.cell_size_m)
    print(f"{lab_map.width}x{lab_map.height} cells ({size_m[0]:g} m x {size_m[1]:g} m), "
          f"{len(lab_map.equipment_ids)} equipment positions, robot speed {layout.robot_speed_m_per_min:g} m/min\n")
    print(lab_map.render(marks))
    print(f"\n{LEGEND}{route_line}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
