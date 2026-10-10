"""Liveness and readiness probes."""

from typing import Literal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from sahayak.api.errors import ErrorEnvelope, error_response
from sahayak.api.readiness import run_checks

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"


class ReadyResponse(BaseModel):
    status: Literal["ok"] = "ok"
    checks: dict[str, Literal["ok"]]


@router.get("/healthz", summary="Liveness probe")
async def healthz() -> HealthResponse:
    """The process is up. Does not touch dependencies."""
    return HealthResponse()


@router.get(
    "/readyz",
    summary="Readiness probe",
    response_model=ReadyResponse,
    responses={503: {"model": ErrorEnvelope, "description": "A dependency is unavailable"}},
)
async def readyz(request: Request) -> ReadyResponse | JSONResponse:
    """Postgres and Redis are reachable."""
    results = await run_checks(request.app.state.readiness_checks)
    if all(results.values()):
        return ReadyResponse(checks=dict.fromkeys(results, "ok"))
    return error_response(
        request,
        503,
        "not_ready",
        "One or more dependencies are unavailable.",
        details={name: "ok" if ok else "unavailable" for name, ok in results.items()},
    )
