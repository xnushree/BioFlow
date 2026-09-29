"""Experiments and plates."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from bioflow.api.dependencies import service
from bioflow.service import SimulationService

router = APIRouter(tags=["experiments"])


class ExperimentRequest(BaseModel):
    experiment_id: str = Field(min_length=1, examples=["EXP100"])
    protocol: str = Field(examples=["basic_experiment"])
    plates: int = Field(ge=1, examples=[5])
    priority: int = Field(default=0, ge=0)
    deadline_in_min: float | None = Field(default=None, gt=0, description="relative to the current simulation time")


@router.get("/experiments")
def list_experiments(svc: SimulationService = Depends(service)) -> list[dict[str, Any]]:
    return svc.experiments()


@router.post("/experiments", status_code=201)
def submit_experiment(request: ExperimentRequest, svc: SimulationService = Depends(service)) -> dict[str, Any]:
    return svc.submit_experiment(request.experiment_id, request.protocol, request.plates,
                                 request.priority, request.deadline_in_min)


@router.get("/experiments/{experiment_id}")
def get_experiment(experiment_id: str, svc: SimulationService = Depends(service)) -> dict[str, Any]:
    return svc.experiment(experiment_id)


@router.get("/plates")
def list_plates(experiment_id: str | None = None, state: str | None = None,
                svc: SimulationService = Depends(service)) -> list[dict[str, Any]]:
    return svc.plates(experiment_id, state)
