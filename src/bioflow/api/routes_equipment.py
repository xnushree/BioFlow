"""Equipment and robots."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from bioflow.api.dependencies import service
from bioflow.service import SimulationService

router = APIRouter(tags=["equipment"])


@router.get("/equipment")
def list_equipment(svc: SimulationService = Depends(service)) -> list[dict[str, Any]]:
    return svc.equipment()


@router.get("/equipment/{equipment_id}")
def get_equipment(equipment_id: str, svc: SimulationService = Depends(service)) -> dict[str, Any]:
    return svc.equipment_item(equipment_id)


@router.get("/robots")
def list_robots(svc: SimulationService = Depends(service)) -> list[dict[str, Any]]:
    """Robots with grid position and remaining planned path (map-based scenarios)."""
    return svc.robots()
