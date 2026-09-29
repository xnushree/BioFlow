"""2D digital-twin drawing, derived entirely from the live simulation state.

Nothing here is animated independently: equipment colours come from each
machine's state machine, robot markers from the cells they currently hold,
paths from their remaining A* plan, and counts from actual plate occupancy.
"""

from __future__ import annotations

from typing import Any

import matplotlib

matplotlib.use("Agg")  # render off-screen for the web page

import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.figure import Figure  # noqa: E402
from matplotlib.patches import Circle, Rectangle  # noqa: E402

COLORS = {
    "floor": "#f7f7f5", "zone": "#fff3c4", "blocked": "#555555", "access": "#1f77b4", "parking": "#bbbbbb",
    "normal": "#b7e4c7", "busy": "#74c0fc", "full": "#ffd8a8", "fault": "#ff6b6b", "recovery": "#ffa94d",
    "out_of_service": "#ced4da", "robot_idle": "#868e96", "robot_busy": "#1971c2", "robot_fault": "#e03131",
    "path": "#1971c2",
}
FAULT_STATES = {"FAULT", "ENVIRONMENTAL_FAULT", "SAFE_STOP"}
BUSY_STATES = {"PROCESSING", "OCCUPIED"}


def equipment_color(item: dict[str, Any]) -> str:
    state = str(item["state"])
    if state in FAULT_STATES:
        return COLORS["fault"]
    if state == "RECOVERY":
        return COLORS["recovery"]
    if not item.get("in_service", True):
        return COLORS["out_of_service"]
    if state == "FULL":
        return COLORS["full"]
    if state in BUSY_STATES:
        return COLORS["busy"]
    return COLORS["normal"]


def robot_color(robot: dict[str, Any]) -> str:
    state = str(robot["state"])
    if state in FAULT_STATES or state == "RECOVERY" or not robot.get("in_service", True):
        return COLORS["robot_fault"]
    return COLORS["robot_idle"] if state == "IDLE" else COLORS["robot_busy"]


def draw_twin(plan: dict[str, Any], equipment: list[dict[str, Any]], robots: list[dict[str, Any]],
              show_paths: bool = True) -> Figure:
    width, height = plan["width"], plan["height"]
    figure, axes = plt.subplots(figsize=(min(14, width * 0.45), min(10, height * 0.45)))
    axes.add_patch(Rectangle((0, 0), width, height, color=COLORS["floor"]))
    for zone in plan["zones"]:
        axes.add_patch(Rectangle((zone["x"], zone["y"]), zone["width"], zone["height"], color=COLORS["zone"]))
    for area in plan["blocked"]:
        axes.add_patch(Rectangle((area["x"], area["y"]), area["width"], area["height"], color=COLORS["blocked"]))
    for x, y in plan["parking"]:
        axes.add_patch(Rectangle((x + 0.2, y + 0.2), 0.6, 0.6, fill=False, edgecolor=COLORS["parking"], lw=1))

    by_id = {item["equipment_id"]: item for item in equipment}
    for eid, box in plan["equipment"].items():
        item = by_id.get(eid)
        if item is None:
            continue
        axes.add_patch(Rectangle((box["x"], box["y"]), box["width"], box["height"],
                                 facecolor=equipment_color(item), edgecolor="#343a40", lw=1.2))
        label = eid.replace("_", " ")
        if "capacity" in item:
            label += f"\n{item['occupancy']}/{item['capacity']}"
        axes.text(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2, label,
                  ha="center", va="center", fontsize=7, weight="bold")
        ax, ay = box["access"]
        axes.plot(ax + 0.5, ay + 0.5, marker="+", color=COLORS["access"], markersize=6)

    for robot in robots:
        if robot.get("cell") is None:
            continue
        x, y = robot["cell"]
        if show_paths and robot.get("path"):
            xs = [x + 0.5] + [c[0] + 0.5 for c in robot["path"]]
            ys = [y + 0.5] + [c[1] + 0.5 for c in robot["path"]]
            axes.plot(xs, ys, color=COLORS["path"], lw=1, alpha=0.5, linestyle="--")
        axes.add_patch(Circle((x + 0.5, y + 0.5), 0.42, color=robot_color(robot), zorder=3))
        tag = robot["equipment_id"].split("_")[-1]
        axes.text(x + 0.5, y + 0.5, tag, ha="center", va="center", color="white", fontsize=6, zorder=4)
        if robot.get("carrying"):
            axes.add_patch(Rectangle((x + 0.62, y + 0.05), 0.33, 0.25, color="#fab005", zorder=4))

    axes.set_xlim(0, width)
    axes.set_ylim(height, 0)  # row 0 at the top, matching the map coordinates
    axes.set_aspect("equal")
    axes.set_xticks([])
    axes.set_yticks([])
    figure.tight_layout()
    return figure


LEGEND = (
    "**Equipment:** green = available, blue = busy, peach = full, red = fault, orange = recovering, "
    "grey = out of service. **Robots:** grey = idle, blue = working, red = fault/out of service, "
    "yellow tag = carrying a plate; dashed line = remaining planned path. "
    "Yellow floor = slow zone, dark = blocked, outlined squares = parking, + = access point."
)
