"""Dashboard tests: the Streamlit app runs headlessly (AppTest) on real simulation state."""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from bioflow.service import SimulationService

ROOT = Path(__file__).parents[2]
APP = str(ROOT / "dashboard" / "app.py")
PAGES = ["Overview", "Digital twin", "Experiments", "Equipment", "Robots", "Scheduler", "Faults", "Analytics"]


@pytest.fixture(autouse=True)
def run_from_project_root(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(ROOT)


def app_with(service: SimulationService | None = None) -> AppTest:
    app = AppTest.from_file(APP, default_timeout=60)
    if service is not None:
        app.session_state["service"] = service
    return app


def loaded_service(scenario: str = "multiple_failures", minutes: float = 1000) -> SimulationService:
    service = SimulationService()
    service.load(Path(f"simulation/scenarios/{scenario}.yaml"))
    service.step(minutes)
    return service


def test_empty_dashboard_asks_for_a_scenario() -> None:
    app = app_with().run()

    assert not app.exception
    assert "Load a scenario" in app.info[0].value


@pytest.mark.parametrize("page", PAGES)
def test_every_page_renders_live_state(page: str) -> None:
    app = app_with(loaded_service())
    app.run()
    app.sidebar.radio[0].set_value(page).run()

    assert not app.exception, app.exception
    assert app.title[0].value == page


def test_overview_numbers_come_from_the_simulation() -> None:
    service = loaded_service("basic_demo", minutes=800)
    app = app_with(service).run()

    metrics = {m.label: m.value for m in app.metric}
    status = service.status()
    assert metrics["Tasks completed"] == f"{status.tasks_completed}/{status.tasks_total}"


def test_twin_page_without_a_floor_plan_explains_why() -> None:
    import yaml

    data = yaml.safe_load((ROOT / "simulation/scenarios/basic_demo.yaml").read_text(encoding="utf-8"))
    data.pop("laboratory_config")
    data["travel_time_min"] = 2.0
    path = ROOT / "results" / "no_map_scenario.yaml"
    path.parent.mkdir(exist_ok=True)
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    try:
        service = SimulationService()
        service.load(path)
        app = app_with(service).run()
        app.sidebar.radio[0].set_value("Digital twin").run()
        assert "no floor plan" in app.info[0].value
    finally:
        path.unlink()


def test_twin_drawing_reflects_equipment_and_robot_state() -> None:
    import matplotlib.pyplot as plt

    from components.twin import COLORS, draw_twin, equipment_color, robot_color  # type: ignore[import-not-found]

    service = loaded_service("basic_demo", minutes=5)
    figure = draw_twin(service.floor_plan(), service.equipment(), service.robots())
    assert figure.axes[0].patches  # something was drawn
    plt.close(figure)
    assert equipment_color({"state": "ENVIRONMENTAL_FAULT"}) == COLORS["fault"]
    assert equipment_color({"state": "AVAILABLE", "in_service": False}) == COLORS["out_of_service"]
    assert robot_color({"state": "IDLE"}) == COLORS["robot_idle"]
    assert robot_color({"state": "MOVING"}) == COLORS["robot_busy"]
