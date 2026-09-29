"""FastAPI application.

Run the server (from the project root):
    uvicorn bioflow.api.main:app --reload
Then open http://127.0.0.1:8000/docs for the interactive API documentation.

Optionally preload a scenario:
    set BIOFLOW_SCENARIO=simulation/scenarios/basic_demo.yaml   (PowerShell: $env:BIOFLOW_SCENARIO=...)
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI

from bioflow import __version__
from bioflow.api import routes_equipment, routes_experiments, routes_faults, routes_simulation
from bioflow.api.dependencies import install_error_handlers
from bioflow.service import SimulationService


def create_app(svc: SimulationService | None = None) -> FastAPI:
    """Build the app around ``svc`` (a fresh service by default); tests inject their own."""
    app = FastAPI(
        title="BioFlow-X",
        version=__version__,
        description="Digital twin and supervisory control of a simulated cell-culture laboratory.",
    )
    app.state.service = svc or SimulationService()
    install_error_handlers(app)
    for module in (routes_simulation, routes_experiments, routes_equipment, routes_faults):
        app.include_router(module.router)

    @app.get("/health", tags=["simulation"])
    def health() -> dict[str, Any]:
        return {"status": "ok", "version": __version__}

    return app


def _default_app() -> FastAPI:
    app = create_app()
    scenario = os.environ.get("BIOFLOW_SCENARIO")
    if scenario:
        app.state.service.load(Path(scenario))
    return app


app = _default_app()
