"""Simulation lifecycle: load a scenario, start, stop, step, and observe status, metrics and events."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from bioflow.api.dependencies import service
from bioflow.service import SimulationService

router = APIRouter(tags=["simulation"])


class LoadRequest(BaseModel):
    scenario: str = Field(description="scenario YAML path, relative to the server's working directory",
                          examples=["simulation/scenarios/basic_demo.yaml"])
    scheduler: str | None = Field(default=None, examples=["cost"])


class StartRequest(BaseModel):
    speed: float | None = Field(default=None, gt=0,
                                description="simulated minutes per real second; omit to run flat out")


class StepRequest(BaseModel):
    minutes: float = Field(gt=0)


@router.get("/simulation/status")
def status(svc: SimulationService = Depends(service)) -> dict[str, Any]:
    return asdict(svc.status())


@router.post("/simulation/load")
def load(request: LoadRequest, svc: SimulationService = Depends(service)) -> dict[str, Any]:
    return asdict(svc.load(Path(request.scenario), request.scheduler))


@router.post("/simulation/start")
def start(request: StartRequest | None = None, svc: SimulationService = Depends(service)) -> dict[str, Any]:
    return asdict(svc.start(request.speed if request else None))


@router.post("/simulation/stop")
def stop(svc: SimulationService = Depends(service)) -> dict[str, Any]:
    return asdict(svc.pause())


@router.post("/simulation/step")
def step(request: StepRequest, svc: SimulationService = Depends(service)) -> dict[str, Any]:
    return asdict(svc.step(request.minutes))


@router.get("/metrics")
def metrics(svc: SimulationService = Depends(service)) -> dict[str, Any]:
    return svc.metrics()


@router.get("/events")
def events(
    event_type: str | None = Query(default=None, alias="type"),
    source: str | None = None,
    limit: int = Query(default=100, ge=1, le=10_000),
    svc: SimulationService = Depends(service),
) -> list[dict[str, Any]]:
    return svc.events(event_type, source, limit)
