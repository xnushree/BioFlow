"""BioFlow-X dashboard.

Run from the project root:
    streamlit run dashboard/app.py

The sidebar holds the simulation controls (load a scenario, start/pause, step)
and page navigation. Pages read the live simulation; with auto-refresh on,
the page redraws about once a second while the simulation runs.
"""

from __future__ import annotations

import time

import streamlit as st

from bioflow.core.exceptions import BioFlowError
from bioflow.scheduling.registry import SCHEDULERS

from components.data import get_service, is_loaded, scenario_files, sim_clock
from components.pages import PAGES

REFRESH_SECONDS = 1.0

st.set_page_config(page_title="BioFlow-X", layout="wide")
service = get_service()


def controls() -> bool:
    """Sidebar simulation controls. Returns whether the page should keep refreshing."""
    st.sidebar.title("BioFlow-X")
    st.sidebar.caption("Digital twin of a simulated cell-culture lab")
    with st.sidebar.expander("Scenario", expanded=not is_loaded(service)):
        scenario = st.selectbox("Scenario file", scenario_files(), index=0)
        scheduler = st.selectbox("Scheduler", ["(scenario default)", *sorted(SCHEDULERS)])
        if st.button("Load", type="primary", width="stretch"):
            _run(lambda: service.load(scenario, None if scheduler.startswith("(") else scheduler))

    status = service.status()
    st.sidebar.markdown(f"**State:** `{status.state}`  \n**Time:** {sim_clock(status.sim_time)}  \n"
                        f"**Scheduler:** `{status.scheduler or '-'}`")
    if status.error:
        st.sidebar.error(status.error)
    if not is_loaded(service):
        return False

    speed = st.sidebar.select_slider("Speed (simulated minutes per second)",
                                     options=[1, 5, 15, 60, 240, 1000, "max"], value=60)
    cols = st.sidebar.columns(2)
    if cols[0].button("Start", width="stretch", disabled=status.state in ("RUNNING", "FINISHED")):
        _run(lambda: service.start(None if speed == "max" else float(speed)))
    if cols[1].button("Pause", width="stretch", disabled=status.state != "RUNNING"):
        _run(service.pause)
    minutes = st.sidebar.number_input("Step (minutes)", min_value=1, value=60, step=30)
    if st.sidebar.button("Step", width="stretch", disabled=status.state in ("RUNNING", "FINISHED")):
        _run(lambda: service.step(float(minutes)))
    auto = st.sidebar.toggle("Auto-refresh while running", value=True)
    return auto and service.status().state == "RUNNING"


def _run(action) -> None:  # noqa: ANN001
    try:
        action()
    except BioFlowError as error:
        st.sidebar.error(str(error))


keep_refreshing = controls()
page = st.sidebar.radio("Page", list(PAGES))

st.title(page)
if is_loaded(service):
    PAGES[page](service)
else:
    st.info("Load a scenario from the sidebar to start. `basic_demo` is a good first choice; "
            "`multiple_failures` shows fault detection and recovery.")

if keep_refreshing:
    time.sleep(REFRESH_SECONDS)
    st.rerun()
