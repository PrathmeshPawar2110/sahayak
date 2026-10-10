import httpx
import pytest
from fastapi import FastAPI
from pydantic import BaseModel

from sahayak.api.errors import AppError


class Payload(BaseModel):
    age: int


@pytest.fixture
def app(app: FastAPI) -> FastAPI:
    @app.get("/boom/app-error")
    async def app_error() -> None:
        raise AppError("scheme_not_found", "No such scheme.", status_code=404)

    @app.get("/boom/unexpected")
    async def unexpected() -> None:
        raise RuntimeError("database password is hunter2")

    @app.post("/boom/validate")
    async def validate(payload: Payload) -> Payload:
        return payload

    return app


async def test_app_error_uses_envelope(client: httpx.AsyncClient) -> None:
    response = await client.get("/boom/app-error")

    assert response.status_code == 404
    error = response.json()["error"]
    assert error["code"] == "scheme_not_found"
    assert error["message"] == "No such scheme."
    assert error["request_id"] == response.headers["X-Request-ID"]


async def test_unknown_route_uses_envelope(client: httpx.AsyncClient) -> None:
    response = await client.get("/does-not-exist")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


async def test_wrong_method_uses_envelope(client: httpx.AsyncClient) -> None:
    response = await client.post("/healthz")

    assert response.status_code == 405
    assert response.json()["error"]["code"] == "method_not_allowed"


async def test_validation_error_hides_submitted_values(client: httpx.AsyncClient) -> None:
    response = await client.post("/boom/validate", json={"age": "my aadhaar 234567890123"})

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    assert error["details"][0]["loc"] == ["body", "age"]
    assert "234567890123" not in response.text


async def test_unexpected_error_is_500_without_internals(client: httpx.AsyncClient) -> None:
    response = await client.get("/boom/unexpected")

    assert response.status_code == 500
    error = response.json()["error"]
    assert error == {
        "code": "internal_error",
        "message": "An unexpected error occurred.",
        "request_id": response.headers["X-Request-ID"],
    }
    assert "hunter2" not in response.text
