"""Benchmark charts (static PNG for reports and the README).

Form: grouped bars, since the question is "how much, per workload, per scheduler".
Error bars show the standard deviation across seeds, so the reader can see
whether a gap between schedulers is larger than seed-to-seed noise.

Colour: one fixed categorical hue per scheduler (never by rank), from a
palette validated for colour-vision deficiency. Two hues are below 3:1
contrast on white, so every chart has a legend and the report carries the
full numeric tables.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.axes import Axes  # noqa: E402

from bioflow.analytics.performance import METRICS  # noqa: E402

SCHEDULER_COLORS = {"fifo": "#2a78d6", "priority": "#eb6834", "deadline": "#1baf7a", "cost": "#eda100"}
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_SECONDARY = "#52514e"
GRID = "#e1e0d9"
BASELINE = "#c3c2b7"
GROUP_WIDTH = 0.8


def _style(axes: Axes, title: str) -> None:
    axes.set_facecolor(SURFACE)
    axes.set_title(title, loc="left", color=INK, fontsize=11)
    axes.grid(axis="y", color=GRID, linewidth=0.8)
    axes.set_axisbelow(True)
    for side in ("top", "right", "left"):
        axes.spines[side].set_visible(False)
    axes.spines["bottom"].set_color(BASELINE)
    axes.tick_params(colors=INK_SECONDARY, labelsize=9, length=0)


def grouped_bars(axes: Axes, aggregated: Sequence[dict[str, Any]], metric: str,
                 workloads: Sequence[str], schedulers: Sequence[str]) -> None:
    label, lower_is_better = METRICS[metric]
    width = GROUP_WIDTH / len(schedulers)
    for index, scheduler in enumerate(schedulers):
        rows = {r["workload"]: r for r in aggregated if r["scheduler"] == scheduler}
        xs = [w + (index - (len(schedulers) - 1) / 2) * width for w in range(len(workloads))]
        means = [rows[w][metric] if w in rows else 0.0 for w in workloads]
        spreads = [rows[w][f"{metric}_std"] if w in rows else 0.0 for w in workloads]
        axes.bar(xs, means, width=width * 0.9, color=SCHEDULER_COLORS.get(scheduler, INK_SECONDARY),
                 edgecolor=SURFACE, linewidth=1, label=scheduler, zorder=2)
        axes.errorbar(xs, means, yerr=spreads, fmt="none", ecolor=INK_SECONDARY, elinewidth=1, capsize=2, zorder=3)
    axes.set_xticks(range(len(workloads)), [f"Workload {w}" for w in workloads])
    _style(axes, f"{label}  ({'lower' if lower_is_better else 'higher'} is better)")


def save_metric_chart(aggregated: Sequence[dict[str, Any]], metric: str, path: Path,
                      workloads: Sequence[str], schedulers: Sequence[str]) -> Path:
    figure, axes = plt.subplots(figsize=(8, 4), facecolor=SURFACE)
    grouped_bars(axes, aggregated, metric, workloads, schedulers)
    axes.legend(title="Scheduler", frameon=False, fontsize=9, title_fontsize=9, loc="upper left",
                bbox_to_anchor=(1.0, 1.0))
    figure.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(path, dpi=140, facecolor=SURFACE)
    plt.close(figure)
    return path


def save_overview(aggregated: Sequence[dict[str, Any]], path: Path, workloads: Sequence[str],
                  schedulers: Sequence[str], metrics: Sequence[str]) -> Path:
    """Small multiples: one panel per metric, each on its own axis (never a shared dual axis)."""
    columns = 2
    rows = (len(metrics) + columns - 1) // columns
    figure, grid = plt.subplots(rows, columns, figsize=(12, 3.4 * rows), facecolor=SURFACE, squeeze=False)
    for axes, metric in zip(grid.flat, metrics, strict=False):
        grouped_bars(axes, aggregated, metric, workloads, schedulers)
    for axes in list(grid.flat)[len(metrics):]:
        axes.set_visible(False)
    handles, labels = grid.flat[0].get_legend_handles_labels()
    figure.legend(handles, labels, title="Scheduler", frameon=False, ncol=len(schedulers), loc="upper center",
                  fontsize=9, title_fontsize=9)
    figure.tight_layout(rect=(0, 0, 1, 0.94))
    path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(path, dpi=140, facecolor=SURFACE)
    plt.close(figure)
    return path
