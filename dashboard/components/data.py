"""Data access for the dashboard: one SimulationService per browser session, plus table helpers.

The dashboard reads the same SimulationService as the REST API, in-process, so a
single command starts everything. Pointing it at a remote API instead would
only mean replacing this module.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from bioflow.core.clock import format_sim_time
from bioflow.service import SimulationService

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCENARIO_DIR = PROJECT_ROOT / "simulation" / "scenarios"


def get_service() -> SimulationService:
    """The session's live simulation (kept across Streamlit reruns)."""
    if "service" not in st.session_state:
        st.session_state.service = SimulationService()
    return st.session_state.service


def scenario_files() -> list[str]:
    return sorted(str(p.relative_to(PROJECT_ROOT)).replace("\\", "/") for p in SCENARIO_DIR.glob("*.yaml"))


def is_loaded(service: SimulationService) -> bool:
    return service.status().state != "EMPTY"


def sim_clock(minutes: float) -> str:
    return f"{format_sim_time(minutes)} ({minutes:,.1f} min)"


def table(rows: list[dict[str, Any]], columns: list[str] | None = None) -> pd.DataFrame:
    frame = pd.DataFrame(rows)
    if columns is not None and not frame.empty:
        frame = frame[[c for c in columns if c in frame.columns]]
    return frame


def pct(value: float | None) -> str:
    return "-" if value is None else f"{value:.0%}"
