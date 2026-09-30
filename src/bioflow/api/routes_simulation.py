"""Simulation lifecycle: load a scenario, start, stop, step, and observe status, metrics and events."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from bioflow.api.dependencies import Service

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
def status(svc: Service) -> dict[str, Any]:
    return asdict(svc.status())


@router.post("/simulation/load")
def load(request: LoadRequest, svc: Service) -> dict[str, Any]:
    return asdict(svc.load(Path(request.scenario), request.scheduler))


@router.post("/simulation/start")
def start(svc: Service, request: StartRequest | None = None) -> dict[str, Any]:
    return asdict(svc.start(request.speed if request else None))


@router.post("/simulation/stop")
def stop(svc: Service) -> dict[str, Any]:
    return asdict(svc.pause())


@router.post("/simulation/step")
def step(request: StepRequest, svc: Service) -> dict[str, Any]:
    return asdict(svc.step(request.minutes))


@router.get("/metrics")
def metrics(svc: Service) -> dict[str, Any]:
    return svc.metrics()


@router.get("/events")
def events(
    svc: Service,
    event_type: Annotated[str | None, Query(alias="type")] = None,
    source: str | None = None,
    limit: Annotated[int, Query(ge=1, le=10_000)] = 100,
) -> list[dict[str, Any]]:
    return svc.events(event_type, source, limit)
