"""One function per dashboard page. Each reads the live service and renders; none holds state."""

from __future__ import annotations

import matplotlib.pyplot as plt
import pandas as pd
import streamlit as st

from bioflow.core.exceptions import BioFlowError
from bioflow.faults.fault import FaultSpec, FaultType, Severity
from bioflow.service import SimulationService

from components.data import pct, sim_clock, table
from components.twin import LEGEND, draw_twin


def overview(service: SimulationService) -> None:
    status = service.status()
    experiments = service.experiments()
    equipment = service.equipment()
    metrics = service.metrics()
    faults = service.faults()
    plates = service.plates()

    cols = st.columns(4)
    cols[0].metric("Simulation time", sim_clock(status.sim_time))
    cols[1].metric("Tasks completed", f"{status.tasks_completed}/{status.tasks_total}")
    cols[2].metric("Active experiments", sum(e["status"] in ("SUBMITTED", "RUNNING") for e in experiments))
    cols[3].metric("Active plates", sum(p["state"] not in ("ARCHIVED", "DISPOSED", "QUARANTINED") for p in plates))
    cols = st.columns(4)
    non_robot = [e for e in equipment if e["kind"] != "ROBOT"]
    cols[0].metric("Equipment available", f"{sum(e['operational'] and e['in_service'] for e in non_robot)}"
                                          f"/{len(non_robot)}")
    cols[1].metric("Robot utilization", pct(metrics["mean_robot_utilization"]))
    cols[2].metric("Ready queue", service.scheduler_view(limit=0)["ready"])
    cols[3].metric("Active faults", sum(d["active"] for d in faults["detections"]))

    st.subheader("Experiment progress")
    st.dataframe(table(experiments, ["experiment_id", "protocol", "status", "priority", "plate_count",
                                     "deadline", "late", "tasks"]), hide_index=True, width="stretch")


def digital_twin(service: SimulationService) -> None:
    plan = service.floor_plan()
    if plan is None:
        st.info("This scenario has no floor plan (fixed travel times), so there is no map to draw. "
                "Load a scenario with `laboratory_config`, e.g. basic_demo or robot_congestion.")
        return
    show_paths = st.toggle("Show planned robot paths", value=True)
    figure = draw_twin(plan, service.equipment(), service.robots(), show_paths)
    st.pyplot(figure, width="stretch")
    plt.close(figure)
    st.caption(LEGEND)


def experiments_page(service: SimulationService) -> None:
    experiments = service.experiments()
    st.dataframe(table(experiments, ["experiment_id", "protocol", "status", "priority", "plate_count",
                                     "submitted_at", "deadline", "late", "tasks"]), hide_index=True, width="stretch")
    if experiments:
        chosen = st.selectbox("Experiment", [e["experiment_id"] for e in experiments])
        detail = service.experiment(chosen)
        st.markdown("**Protocol steps**")
        st.dataframe(pd.DataFrame(detail["steps"]), hide_index=True)
        st.markdown("**Plates**")
        st.dataframe(table(service.plates(experiment_id=chosen),
                           ["plate_id", "state", "location_id", "contamination"]), hide_index=True, width="stretch")

    with st.form("submit_experiment"):
        st.markdown("**Submit an experiment now**")
        cols = st.columns(5)
        experiment_id = cols[0].text_input("ID", value="EXP100")
        protocol = cols[1].selectbox("Protocol", ["basic_experiment", "imaging_experiment", "stress_test"])
        plates = cols[2].number_input("Plates", min_value=1, max_value=200, value=4)
        priority = cols[3].number_input("Priority", min_value=0, max_value=10, value=1)
        deadline = cols[4].number_input("Deadline in (min, 0 = none)", min_value=0, value=0)
        if st.form_submit_button("Submit"):
            _act(lambda: service.submit_experiment(experiment_id, protocol, int(plates), int(priority),
                                                   float(deadline) or None), f"Submitted {experiment_id}")


def equipment_page(service: SimulationService) -> None:
    metrics = service.metrics()
    utilization = {**metrics["station_utilization"], **metrics["incubator_utilization"],
                   **metrics["robot_utilization"]}
    rows = [{**e, "utilization": pct(utilization.get(e["equipment_id"])),
             "plates": ", ".join(e.get("plate_ids", [])[:5]) + (" ..." if len(e.get("plate_ids", [])) > 5 else ""),
             "faults": ", ".join(e["active_detections"])}
            for e in service.equipment() if e["kind"] != "ROBOT"]
    st.dataframe(table(rows, ["equipment_id", "kind", "state", "operational", "in_service", "occupancy",
                              "capacity", "processing_plate_id", "utilization", "faults", "plates"]),
                 hide_index=True, width="stretch")


def robots_page(service: SimulationService) -> None:
    utilization = service.metrics()["robot_utilization"]
    rows = [{**r, "utilization": pct(utilization.get(r["equipment_id"])),
             "job": f"{r['job']['plate_id']}: {r['job']['source']} -> {r['job']['destination']}" if r["job"] else "",
             "faults": ", ".join(r["active_detections"]), "path_cells": len(r["path"])}
            for r in service.robots()]
    st.dataframe(table(rows, ["equipment_id", "state", "in_service", "location_id", "cell", "carrying", "job",
                              "path_cells", "utilization", "faults"]), hide_index=True, width="stretch")


def scheduler_page(service: SimulationService) -> None:
    view = service.scheduler_view()
    st.markdown(f"**Policy:** `{view['scheduler']}` - **ready tasks:** {view['ready']}")
    st.caption("Ready tasks in the order the policy would try them now. Tasks further down still start "
               "immediately if the resources they need are free (no head-of-line blocking).")
    st.dataframe(table(view["queue"]), hide_index=True, width="stretch")


def faults_page(service: SimulationService) -> None:
    faults = service.faults()
    st.subheader("Detected faults")
    st.dataframe(table(faults["detections"], ["detection_id", "fault_type", "equipment_id", "detected_at",
                                              "cleared_at", "active", "evidence"]), hide_index=True, width="stretch")
    st.subheader("Recoveries")
    st.dataframe(table(faults["recoveries"], ["recovery_id", "fault_type", "equipment_id", "started_at",
                                              "completed_at", "duration_min", "rescheduled_tasks", "blocked_reason",
                                              "unrecoverable", "actions"]), hide_index=True, width="stretch")
    with st.expander("Injected faults (ground truth: not visible to the control system)"):
        st.dataframe(table(faults["injected_ground_truth"]), hide_index=True, width="stretch")

    with st.form("inject_fault"):
        st.markdown("**Inject a fault now**")
        cols = st.columns(4)
        fault_type = cols[0].selectbox("Type", [t.value for t in FaultType])
        equipment_id = cols[1].selectbox("Equipment", [e["equipment_id"] for e in service.equipment()])
        duration = cols[2].number_input("Repaired after (min, 0 = never)", min_value=0, value=60)
        severity = cols[3].selectbox("Severity", [s.value for s in Severity], index=2)
        if st.form_submit_button("Inject"):
            spec = FaultSpec(FaultType(fault_type), equipment_id, 0.0, float(duration) or None, Severity(severity))
            _act(lambda: service.inject_fault(spec), f"Injected {fault_type} on {equipment_id}")


def analytics_page(service: SimulationService) -> None:
    metrics = service.metrics()
    series = service.timeseries()
    cols = st.columns(4)
    cols[0].metric("Throughput", f"{metrics['throughput_plates_per_hour']:.2f} plates/h")
    cols[1].metric("Mean task wait", f"{metrics['mean_task_wait_min']:.1f} min")
    cols[2].metric("Max task wait", f"{metrics['max_task_wait_min']:.1f} min")
    cols[3].metric("Tasks rescheduled", metrics["tasks_rescheduled"])

    if series["completions"]:
        st.markdown("**Completed tasks over time**")
        st.line_chart(pd.DataFrame(series["completions"]).set_index("sim_time"))
    if series["ready_queue"]:
        st.markdown("**Ready-queue length**")
        st.area_chart(pd.DataFrame(series["ready_queue"]).set_index("sim_time"))
    utilization = {**metrics["robot_utilization"], **metrics["station_utilization"],
                   **metrics["incubator_utilization"]}
    if utilization:
        st.markdown("**Utilization by equipment**")
        st.bar_chart(pd.Series(utilization, name="utilization"))

    recoveries = [r for r in service.faults()["recoveries"] if r["duration_min"] is not None]
    if recoveries:
        st.markdown("**Recovery duration by fault**")
        st.bar_chart(pd.DataFrame(recoveries).set_index("recovery_id")["duration_min"])


def _act(action, success: str) -> None:  # noqa: ANN001
    try:
        action()
        st.success(success)
    except BioFlowError as error:
        st.error(str(error))


PAGES = {
    "Overview": overview,
    "Digital twin": digital_twin,
    "Experiments": experiments_page,
    "Equipment": equipment_page,
    "Robots": robots_page,
    "Scheduler": scheduler_page,
    "Faults": faults_page,
    "Analytics": analytics_page,
}
