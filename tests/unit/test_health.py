import httpx
from fastapi import FastAPI

from sahayak.api.readiness import ReadinessCheck


def _ok() -> ReadinessCheck:
    async def check() -> None:
        return None

    return check


def _failing(exc: Exception) -> ReadinessCheck:
    async def check() -> None:
        raise exc

    return check


async def test_healthz_is_ok(client: httpx.AsyncClient) -> None:
    response = await client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert "X-Request-ID" in response.headers


async def test_readyz_ok_when_all_dependencies_up(app: FastAPI, client: httpx.AsyncClient) -> None:
    app.state.readiness_checks = {"postgres": _ok(), "redis": _ok()}

    response = await client.get("/readyz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "checks": {"postgres": "ok", "redis": "ok"}}


async def test_readyz_503_with_envelope_when_a_dependency_is_down(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    secret_dsn_error = ConnectionError("could not connect to postgresql://user:pw@db")
    app.state.readiness_checks = {"postgres": _failing(secret_dsn_error), "redis": _ok()}

    response = await client.get("/readyz")

    assert response.status_code == 503
    error = response.json()["error"]
    assert error["code"] == "not_ready"
    assert error["details"] == {"postgres": "unavailable", "redis": "ok"}
    assert "pw@db" not in response.text


async def test_readyz_wires_real_checks_for_postgres_and_redis(app: FastAPI) -> None:
    from asgi_lifespan import LifespanManager

    async with LifespanManager(app):
        assert set(app.state.readiness_checks) == {"postgres", "redis"}


async def test_openapi_docs_are_published(client: httpx.AsyncClient) -> None:
    schema = (await client.get("/openapi.json")).json()

    assert schema["info"]["title"] == "Sahayak API"
    assert {"/healthz", "/readyz"} <= set(schema["paths"])
    assert (await client.get("/docs")).status_code == 200


async def test_cors_allows_the_configured_web_origin(client: httpx.AsyncClient) -> None:
    response = await client.get("/healthz", headers={"Origin": "http://localhost:5173"})

    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"


async def test_cors_rejects_other_origins(client: httpx.AsyncClient) -> None:
    response = await client.get("/healthz", headers={"Origin": "https://evil.example"})

    assert "access-control-allow-origin" not in response.headers
