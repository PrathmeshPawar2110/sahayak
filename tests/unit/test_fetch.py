import json
import ssl
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import httpx
import pytest
import respx

from sahayak.ingestion.fetch import (
    DomainRateLimiter,
    Fetcher,
    FetchError,
    crawler_ssl_context,
    parse_rate,
)

UA = "SahayakBot/1.0 (+test)"
PAGE = "https://scheme.gov.in/about.html"
PDF = "https://scheme.gov.in/docs/guidelines(English).pdf"
HTML_BODY = b"<!DOCTYPE html><html><body><h1>Eligibility</h1></body></html>"
PDF_BODY = b"%PDF-1.7\n...binary..."


async def no_sleep(_seconds: float) -> None:
    return None


@pytest.fixture
async def fetcher(tmp_path: Path) -> AsyncIterator[Fetcher]:
    limiter = DomainRateLimiter(0.0, sleep=no_sleep)
    async with Fetcher(tmp_path, user_agent=UA, limiter=limiter) as fetcher:
        yield fetcher


@pytest.fixture
def web() -> Iterator[respx.MockRouter]:
    with respx.mock(assert_all_called=False) as router:
        router.get("https://scheme.gov.in/robots.txt").respond(404)
        yield router


async def test_saves_body_and_sidecar(
    fetcher: Fetcher, web: respx.MockRouter, tmp_path: Path
) -> None:
    web.get(PAGE).respond(
        200, content=HTML_BODY, headers={"content-type": "text/html", "etag": '"v1"'}
    )

    document = await fetcher.fetch(PAGE)

    assert document.kind == "html"
    assert document.not_modified is False
    assert document.path.parent == tmp_path / "scheme.gov.in"
    assert await document.read_bytes() == HTML_BODY
    meta = json.loads(document.path.with_suffix(".meta.json").read_text("utf-8"))
    assert meta["url"] == PAGE
    assert meta["etag"] == '"v1"'
    assert meta["sha256"] == document.sha256
    assert meta["fetched_at"] == document.fetched_at.isoformat()
    assert web.calls.last.request.headers["user-agent"] == UA


async def test_second_fetch_is_conditional_and_reuses_the_copy_on_304(
    fetcher: Fetcher, web: respx.MockRouter
) -> None:
    route = web.get(PAGE)
    route.side_effect = [
        httpx.Response(
            200,
            content=HTML_BODY,
            headers={
                "content-type": "text/html",
                "etag": '"v1"',
                "last-modified": "Wed, 01 Oct 2026 10:00:00 GMT",
            },
        ),
        httpx.Response(304),
    ]

    first = await fetcher.fetch(PAGE)
    second = await fetcher.fetch(PAGE)

    request = route.calls.last.request
    assert request.headers["if-none-match"] == '"v1"'
    assert request.headers["if-modified-since"] == "Wed, 01 Oct 2026 10:00:00 GMT"
    assert second.not_modified is True
    assert second.sha256 == first.sha256
    assert second.path == first.path
    assert second.fetched_at >= first.fetched_at


async def test_changed_content_replaces_the_copy(fetcher: Fetcher, web: respx.MockRouter) -> None:
    web.get(PAGE).side_effect = [
        httpx.Response(200, content=HTML_BODY, headers={"content-type": "text/html"}),
        httpx.Response(
            200, content=HTML_BODY + b"<p>new</p>", headers={"content-type": "text/html"}
        ),
    ]

    first = await fetcher.fetch(PAGE)
    second = await fetcher.fetch(PAGE)

    assert second.sha256 != first.sha256
    assert (await second.read_bytes()).endswith(b"<p>new</p>")


async def test_pdf_is_detected_by_magic_bytes(fetcher: Fetcher, web: respx.MockRouter) -> None:
    web.get(PDF).respond(
        200, content=PDF_BODY, headers={"content-type": "application/octet-stream"}
    )

    document = await fetcher.fetch(PDF)

    assert document.kind == "pdf"
    assert document.path.suffix == ".pdf"


async def test_robots_disallow_is_respected(tmp_path: Path) -> None:
    limiter = DomainRateLimiter(0.0, sleep=no_sleep)
    with respx.mock() as web:
        web.get("https://scheme.gov.in/robots.txt").respond(
            200, text="User-agent: *\nDisallow: /docs/\n", headers={"content-type": "text/plain"}
        )
        page = web.get(PAGE).respond(200, content=HTML_BODY, headers={"content-type": "text/html"})
        async with Fetcher(tmp_path, user_agent=UA, limiter=limiter) as fetcher:
            with pytest.raises(FetchError, match=r"robots\.txt disallows"):
                await fetcher.fetch(PDF)
            await fetcher.fetch(PAGE)

    assert page.called


@pytest.mark.parametrize("robots", [httpx.Response(503), httpx.ConnectTimeout("slow")])
async def test_unreachable_robots_means_disallowed(
    tmp_path: Path, robots: httpx.Response | Exception
) -> None:
    limiter = DomainRateLimiter(0.0, sleep=no_sleep)
    with respx.mock() as web:
        web.get("https://scheme.gov.in/robots.txt").mock(side_effect=[robots, robots])
        async with Fetcher(tmp_path, user_agent=UA, limiter=limiter) as fetcher:
            with pytest.raises(FetchError, match="disallows"):
                await fetcher.fetch(PAGE)


async def test_html_error_page_as_robots_txt_means_no_rules(tmp_path: Path) -> None:
    limiter = DomainRateLimiter(0.0, sleep=no_sleep)
    with respx.mock() as web:
        web.get("https://scheme.gov.in/robots.txt").respond(
            200, text="<html>Disallow: /</html>", headers={"content-type": "text/html"}
        )
        web.get(PAGE).respond(200, content=HTML_BODY, headers={"content-type": "text/html"})
        async with Fetcher(tmp_path, user_agent=UA, limiter=limiter) as fetcher:
            assert (await fetcher.fetch(PAGE)).kind == "html"


@pytest.mark.parametrize(
    ("response", "error"),
    [
        (httpx.Response(404), "HTTP 404"),
        (httpx.Response(200, content=b"{}", headers={"content-type": "application/json"}), "type"),
        (httpx.Response(200, content=b"x" * 2000, headers={"content-type": "text/html"}), "larger"),
    ],
)
async def test_fetch_failures(tmp_path: Path, response: httpx.Response, error: str) -> None:
    limiter = DomainRateLimiter(0.0, sleep=no_sleep)
    with respx.mock() as web:
        web.get("https://scheme.gov.in/robots.txt").respond(404)
        web.get(PAGE).mock(return_value=response)
        async with Fetcher(tmp_path, user_agent=UA, limiter=limiter, max_bytes=1000) as fetcher:
            with pytest.raises(FetchError, match=error):
                await fetcher.fetch(PAGE)


async def test_network_errors_become_fetch_errors(fetcher: Fetcher, web: respx.MockRouter) -> None:
    web.get(PAGE).mock(side_effect=httpx.ConnectTimeout("timed out"))

    with pytest.raises(FetchError, match="ConnectTimeout"):
        await fetcher.fetch(PAGE)


async def test_failed_connect_is_retried_once(fetcher: Fetcher, web: respx.MockRouter) -> None:
    route = web.get(PAGE).mock(
        side_effect=[httpx.ConnectTimeout("timed out"), httpx.Response(200, content=HTML_BODY)]
    )

    document = await fetcher.fetch(PAGE)

    assert route.call_count == 2
    assert document.kind == "html"


async def test_connect_retry_gives_up_after_two_attempts(
    fetcher: Fetcher, web: respx.MockRouter
) -> None:
    route = web.get(PAGE).mock(side_effect=httpx.ConnectError("refused"))

    with pytest.raises(FetchError, match="ConnectError"):
        await fetcher.fetch(PAGE)
    assert route.call_count == 2


async def test_read_timeouts_are_not_retried(fetcher: Fetcher, web: respx.MockRouter) -> None:
    # The request may have reached the server: leave retrying to the next run.
    route = web.get(PAGE).mock(side_effect=httpx.ReadTimeout("slow"))

    with pytest.raises(FetchError, match="ReadTimeout"):
        await fetcher.fetch(PAGE)
    assert route.call_count == 1


async def test_robots_txt_connect_is_retried(tmp_path: Path) -> None:
    limiter = DomainRateLimiter(0.0, sleep=no_sleep)
    with respx.mock() as web:
        robots = web.get("https://scheme.gov.in/robots.txt").mock(
            side_effect=[httpx.ConnectTimeout("timed out"), httpx.Response(404)]
        )
        web.get(PAGE).respond(200, content=HTML_BODY)
        async with Fetcher(tmp_path, user_agent=UA, limiter=limiter) as fetcher:
            await fetcher.fetch(PAGE)
    assert robots.call_count == 2


async def test_rejects_non_http_urls(fetcher: Fetcher) -> None:
    with pytest.raises(FetchError, match="not an http"):
        await fetcher.fetch("file:///etc/passwd")


# ---------------------------------------------------------------- rate limiting


@pytest.mark.parametrize(("rate", "interval"), [("1/s", 1.0), ("30/m", 2.0), ("0.5/s", 2.0)])
def test_parse_rate(rate: str, interval: float) -> None:
    assert parse_rate(rate) == interval


@pytest.mark.parametrize("rate", ["fast", "0/s", "1/d"])
def test_parse_rate_rejects_bad_values(rate: str) -> None:
    with pytest.raises(ValueError, match="invalid rate"):
        parse_rate(rate)


async def test_rate_limiter_spaces_requests_per_host() -> None:
    now = [100.0]
    slept: list[float] = []

    async def sleep(seconds: float) -> None:
        slept.append(seconds)
        now[0] += seconds

    limiter = DomainRateLimiter(1.0, clock=lambda: now[0], sleep=sleep)

    await limiter.wait("a.gov.in")
    await limiter.wait("a.gov.in")
    await limiter.wait("b.gov.in")  # other host: no wait
    now[0] += 5
    await limiter.wait("a.gov.in")  # interval already passed: no wait

    assert slept == [1.0]


def test_crawler_tls_context_adds_intermediates_without_partial_chains() -> None:
    context = crawler_ssl_context()

    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True
    assert not context.verify_flags & ssl.VERIFY_X509_PARTIAL_CHAIN
    subjects = " | ".join(str(cert["subject"]) for cert in context.get_ca_certs())
    for name in ("GoDaddy TLS Intermediate CA DV - R1v1", "'YE2'", "Root YE"):
        assert name in subjects
