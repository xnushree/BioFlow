"""Equipment and robots."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from bioflow.api.dependencies import Service

router = APIRouter(tags=["equipment"])


@router.get("/equipment")
def list_equipment(svc: Service) -> list[dict[str, Any]]:
    return svc.equipment()


@router.get("/equipment/{equipment_id}")
def get_equipment(equipment_id: str, svc: Service) -> dict[str, Any]:
    return svc.equipment_item(equipment_id)


@router.get("/robots")
def list_robots(svc: Service) -> list[dict[str, Any]]:
    """Robots with grid position and remaining planned path (map-based scenarios)."""
    return svc.robots()
