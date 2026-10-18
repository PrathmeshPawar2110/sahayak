from collections.abc import AsyncIterator

import httpx
import pytest
from sqlalchemy import delete, insert
from sqlalchemy.ext.asyncio import AsyncEngine

from sahayak.db.models import Scheme, SchemeLevel, SchemeStatus

PREFIX = "zz-api-"
SCHEMES = [
    ("central", SchemeLevel.CENTRAL, None, SchemeStatus.ACTIVE),
    ("mh", SchemeLevel.STATE, "MH", SchemeStatus.PROVISIONAL),
    ("ka", SchemeLevel.STATE, "KA", SchemeStatus.ACTIVE),
    ("closed", SchemeLevel.CENTRAL, None, SchemeStatus.DISCONTINUED),
    ("archived", SchemeLevel.CENTRAL, None, SchemeStatus.ARCHIVED),
]


@pytest.fixture
async def schemes(db_engine: AsyncEngine) -> AsyncIterator[None]:
    # The API reads through its own connections, so these rows are committed, then removed.
    async with db_engine.begin() as connection:
        for suffix, level, state, status in SCHEMES:
            await connection.execute(
                insert(Scheme).values(
                    id=PREFIX + suffix,
                    name_en=f"Scheme {suffix}",
                    name_hi=f"योजना {suffix}" if suffix == "central" else None,
                    level=level.value,
                    state=state,
                    status=status.value,
                    aliases=["Alias"],
                    documents_required=["Aadhaar card"],
                    application_url=f"https://example.gov.in/{suffix}",
                )
            )
    try:
        yield
    finally:
        async with db_engine.begin() as connection:
            await connection.execute(delete(Scheme).where(Scheme.id.startswith(PREFIX)))


def _ids(response: httpx.Response) -> list[str]:
    return [s["id"] for s in response.json()["schemes"] if s["id"].startswith(PREFIX)]


@pytest.mark.usefixtures("schemes", "database_url")
async def test_list_shows_recommendable_schemes_by_state(client: httpx.AsyncClient) -> None:
    every_state = await client.get("/v1/schemes")
    assert _ids(every_state) == [PREFIX + "central", PREFIX + "ka", PREFIX + "mh"]
    in_mh = await client.get("/v1/schemes", params={"state": "mh"})
    assert _ids(in_mh) == [PREFIX + "central", PREFIX + "mh"]


@pytest.mark.usefixtures("schemes", "database_url")
async def test_names_follow_the_language(client: httpx.AsyncClient) -> None:
    body = (await client.get("/v1/schemes", params={"language": "hi"})).json()
    central = next(s for s in body["schemes"] if s["id"] == PREFIX + "central")
    mh = next(s for s in body["schemes"] if s["id"] == PREFIX + "mh")
    assert central["name"] == "योजना central"
    assert mh["name"] == "Scheme mh"  # no Hindi name: falls back to English
    assert mh["names"] == {"en": "Scheme mh", "hi": "Scheme mh", "mr": "Scheme mh"}


@pytest.mark.usefixtures("schemes", "database_url")
async def test_scheme_detail(client: httpx.AsyncClient) -> None:
    response = await client.get(f"/v1/schemes/{PREFIX}closed")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "discontinued"  # findable by id, never listed
    assert body["documents_required"] == ["Aadhaar card"]
    assert body["official_url"] == "https://example.gov.in/closed"


@pytest.mark.usefixtures("schemes", "database_url")
@pytest.mark.parametrize("scheme_id", [PREFIX + "archived", "no-such-scheme"])
async def test_unknown_and_archived_schemes_are_not_found(
    client: httpx.AsyncClient, scheme_id: str
) -> None:
    response = await client.get(f"/v1/schemes/{scheme_id}")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "scheme_not_found"


@pytest.mark.usefixtures("database_url")
async def test_bad_query_parameters_are_rejected(client: httpx.AsyncClient) -> None:
    assert (await client.get("/v1/schemes", params={"state": "Maharashtra"})).status_code == 422
    assert (await client.get("/v1/schemes", params={"language": "ta"})).status_code == 422
