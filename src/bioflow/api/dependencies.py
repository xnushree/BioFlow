"""Shared API plumbing: access to the service, and mapping platform errors to HTTP responses."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse

from bioflow.core.exceptions import (
    BioFlowError,
    ConfigurationError,
    InvalidTransitionError,
    ProtocolError,
    ResourceError,
    SafetyViolationError,
    SimulationError,
    UnknownEntityError,
    ValidationError,
)
from bioflow.service import SimulationService

# Most specific first: the first matching class decides the status code.
_STATUS_FOR_ERROR: list[tuple[type[BioFlowError], int]] = [
    (UnknownEntityError, 404),
    (ResourceError, 409),
    (SafetyViolationError, 409),
    (InvalidTransitionError, 409),
    (SimulationError, 409),
    (ValidationError, 422),
    (ProtocolError, 422),
    (ConfigurationError, 400),
]


def service(request: Request) -> SimulationService:
    return request.app.state.service


# The live simulation service, injected into route handlers (FastAPI's Annotated dependency style).
Service = Annotated[SimulationService, Depends(service)]


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(BioFlowError)
    async def bioflow_error(request: Request, error: BioFlowError) -> JSONResponse:
        status = next((code for cls, code in _STATUS_FOR_ERROR if isinstance(error, cls)), 400)
        return JSONResponse(status_code=status, content={"error": type(error).__name__, "detail": str(error)})
