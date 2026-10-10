from collections.abc import AsyncIterator

import httpx
import pytest
import structlog
from fastapi import FastAPI, Request

from sahayak.observability.middleware import REQUEST_ID_HEADER, RequestIdMiddleware


def _make_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(RequestIdMiddleware)

    @app.get("/echo")
    async def echo(request: Request) -> dict[str, object]:
        return {
            "state": request.state.request_id,
            "context": structlog.contextvars.get_contextvars().get("request_id"),
        }

    return app


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=_make_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


async def test_generates_request_id_when_missing(client: httpx.AsyncClient) -> None:
    response = await client.get("/echo")

    request_id = response.headers[REQUEST_ID_HEADER]
    assert len(request_id) == 32
    assert response.json() == {"state": request_id, "context": request_id}


async def test_echoes_valid_incoming_request_id(client: httpx.AsyncClient) -> None:
    response = await client.get("/echo", headers={REQUEST_ID_HEADER: "trace-abc.123"})

    assert response.headers[REQUEST_ID_HEADER] == "trace-abc.123"
    assert response.json()["context"] == "trace-abc.123"


@pytest.mark.parametrize("bad", ["has space", "x" * 65, "<script>", "a;b"])
async def test_replaces_unsafe_incoming_request_id(client: httpx.AsyncClient, bad: str) -> None:
    response = await client.get("/echo", headers={REQUEST_ID_HEADER: bad})

    assert response.headers[REQUEST_ID_HEADER] != bad
    assert len(response.headers[REQUEST_ID_HEADER]) == 32
