"""Fault detections, recoveries, and fault injection."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from bioflow.api.dependencies import service
from bioflow.faults.fault import FaultSpec, FaultType, Severity
from bioflow.service import SimulationService

router = APIRouter(tags=["faults"])


class InjectRequest(BaseModel):
    type: FaultType
    equipment: str = Field(examples=["INCUBATOR_01"])
    duration_min: float | None = Field(default=None, gt=0, description="omit for a fault that is never repaired")
    severity: Severity = Severity.MEDIUM
    magnitude: float | None = Field(default=None, description="size of an excursion or slowdown")
    mode: str | None = Field(default=None, description="SENSOR_FAILURE only: 'stuck' or 'dropout'")


@router.get("/faults")
def list_faults(svc: SimulationService = Depends(service)) -> dict[str, list[dict[str, Any]]]:
    """Detections and recoveries (what the control system knows), plus labelled injected ground truth."""
    return svc.faults()


@router.post("/faults/inject", status_code=201)
def inject_fault(request: InjectRequest, svc: SimulationService = Depends(service)) -> dict[str, Any]:
    spec = FaultSpec(
        fault_type=request.type, equipment_id=request.equipment, start_min=0.0,
        duration_min=request.duration_min, severity=request.severity, magnitude=request.magnitude,
        metadata={"mode": request.mode} if request.mode else {},
    )
    return svc.inject_fault(spec)
