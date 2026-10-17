"""Polite downloader for scheme sources (TRD §8 step 1, §8A crawling rules).

Each URL is stored under `data/raw/<host>/<hash>.<ext>` with a JSON sidecar recording the source
URL, fetch date, content hash and HTTP validators. Re-fetching sends conditional headers, so an
unchanged page costs a 304 and keeps the stored copy.
"""

import asyncio
import hashlib
import json
import re
import ssl
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from functools import cache
from importlib import resources
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import anyio
import certifi
import httpx
import structlog

log = structlog.get_logger(__name__)

DocumentKind = Literal["html", "pdf"]
MAX_BYTES = 50 * 1024 * 1024
# A connection that could not be set up is tried once more: no request reached the server, and
# some hosts reliably fail the first connect of a process (seen behind NAT64).
CONNECT_ATTEMPTS = 2
_CONNECT_ERRORS = (httpx.ConnectError, httpx.ConnectTimeout)
_RATE_RE = re.compile(r"^(?P<count>\d+(?:\.\d+)?)/(?P<unit>[smh])$")
_UNIT_SECONDS = {"s": 1.0, "m": 60.0, "h": 3600.0}


class FetchError(Exception):
    """The URL could not be fetched (network, HTTP status, robots.txt, size or type)."""


@dataclass(frozen=True)
class FetchedDocument:
    url: str  # as requested (the manifest URL)
    final_url: str  # after redirects
    kind: DocumentKind
    content_type: str
    sha256: str
    fetched_at: datetime
    path: Path
    not_modified: bool  # True when the server answered 304 and the stored copy was reused

    async def read_bytes(self) -> bytes:
        return await anyio.Path(self.path).read_bytes()


@dataclass
class _Meta:
    url: str
    final_url: str
    kind: DocumentKind
    content_type: str
    sha256: str
    fetched_at: str
    etag: str | None
    last_modified: str | None


@cache
def crawler_ssl_context() -> ssl.SSLContext:
    """certifi roots plus intermediates some government servers fail to send (D-016)."""
    context = ssl.create_default_context(cafile=certifi.where())
    extra = resources.files("sahayak.ingestion").joinpath("extra_intermediates.crt")
    context.load_verify_locations(cadata=extra.read_text("utf-8"))
    # Chains must still end at a root certifi trusts: the extra certificates only fill gaps.
    context.verify_flags &= ~ssl.VERIFY_X509_PARTIAL_CHAIN
    return context


def parse_rate(rate: str) -> float:
    """'1/s' -> 1.0 second between requests; '30/m' -> 2.0."""
    match = _RATE_RE.match(rate)
    if not match or float(match["count"]) <= 0:
        raise ValueError(f"invalid rate {rate!r}; expected like '1/s' or '30/m'")
    return _UNIT_SECONDS[match["unit"]] / float(match["count"])


class DomainRateLimiter:
    """At most one request per `interval` seconds to each host."""

    def __init__(
        self,
        interval_sec: float,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._interval = interval_sec
        self._clock = clock
        self._sleep = sleep
        self._next_slot: dict[str, float] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def wait(self, host: str) -> None:
        async with self._locks.setdefault(host, asyncio.Lock()):
            now = self._clock()
            slot = max(now, self._next_slot.get(host, now))
            self._next_slot[host] = slot + self._interval
            if slot > now:
                await self._sleep(slot - now)


class Fetcher:
    def __init__(
        self,
        raw_dir: Path,
        *,
        user_agent: str,
        rate_limit: str = "1/s",
        timeout_sec: float = 30.0,
        max_bytes: int = MAX_BYTES,
        client: httpx.AsyncClient | None = None,
        limiter: DomainRateLimiter | None = None,
    ) -> None:
        self._raw_dir = raw_dir
        self._max_bytes = max_bytes
        self._limiter = limiter or DomainRateLimiter(parse_rate(rate_limit))
        self._client = client or httpx.AsyncClient(
            timeout=timeout_sec, verify=crawler_ssl_context()
        )
        self._client.headers["User-Agent"] = user_agent
        self._user_agent = user_agent
        self._robots: dict[str, RobotFileParser | None] = {}

    async def __aenter__(self) -> "Fetcher":
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self._client.aclose()

    async def fetch(self, url: str) -> FetchedDocument:
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise FetchError(f"not an http(s) URL: {url}")
        if not await self._allowed(url, parts.scheme, parts.netloc):
            raise FetchError(f"robots.txt disallows {url}")

        path = self._path_for(url)
        meta = await self._read_meta(path)
        headers: dict[str, str] = {}
        if meta is not None and await anyio.Path(path.with_suffix(f".{meta.kind}")).exists():
            if meta.etag:
                headers["If-None-Match"] = meta.etag
            if meta.last_modified:
                headers["If-Modified-Since"] = meta.last_modified

        try:
            return await self._connect_retrying(
                parts.netloc, url, lambda: self._download(url, path, meta, headers)
            )
        except httpx.HTTPError as exc:
            raise FetchError(f"{type(exc).__name__} fetching {url}") from exc

    async def _download(
        self, url: str, path: Path, meta: _Meta | None, headers: dict[str, str]
    ) -> FetchedDocument:
        async with self._client.stream(
            "GET", url, headers=headers, follow_redirects=True
        ) as response:
            if response.status_code == 304 and meta is not None:
                return await self._reuse(path, meta)
            if response.status_code != 200:
                raise FetchError(f"HTTP {response.status_code} for {url}")
            body = await self._read_limited(response, url)
            content_type = response.headers.get("content-type", "")
            kind = _kind(content_type, body, url)
            document = FetchedDocument(
                url=url,
                final_url=str(response.url),
                kind=kind,
                content_type=content_type,
                sha256=hashlib.sha256(body).hexdigest(),
                fetched_at=datetime.now(UTC),
                path=path.with_suffix(f".{kind}"),
                not_modified=False,
            )
            await self._store(document, body, response.headers)
        log.info(
            "fetched", url=url, kind=document.kind, bytes=len(body), sha256=document.sha256[:12]
        )
        return document

    async def _connect_retrying[T](
        self, netloc: str, url: str, request: Callable[[], Awaitable[T]]
    ) -> T:
        """Run `request` after the rate limit, retrying when no connection could be made."""
        for attempt in range(1, CONNECT_ATTEMPTS + 1):
            await self._limiter.wait(netloc)
            try:
                return await request()
            except _CONNECT_ERRORS as exc:
                if attempt == CONNECT_ATTEMPTS:
                    raise
                log.info("connect_retry", url=url, attempt=attempt, error=type(exc).__name__)
        raise AssertionError("unreachable: the loop returns or raises")  # pragma: no cover

    # ------------------------------------------------------------------ robots.txt

    async def _allowed(self, url: str, scheme: str, netloc: str) -> bool:
        if netloc not in self._robots:
            self._robots[netloc] = await self._load_robots(f"{scheme}://{netloc}/robots.txt")
        parser = self._robots[netloc]
        # RFC 9309: unreachable robots.txt (5xx / network) means "assume disallowed".
        return parser is not None and parser.can_fetch(self._user_agent, url)

    async def _load_robots(self, robots_url: str) -> RobotFileParser | None:
        parser = RobotFileParser(robots_url)
        try:
            response = await self._connect_retrying(
                urlsplit(robots_url).netloc,
                robots_url,
                lambda: self._client.get(robots_url, follow_redirects=True),
            )
        except httpx.HTTPError as exc:
            log.warning("robots_unreachable", url=robots_url, error=type(exc).__name__)
            return None
        if response.status_code >= 500:
            log.warning("robots_unreachable", url=robots_url, status=response.status_code)
            return None
        if response.status_code >= 400 or "html" in response.headers.get("content-type", ""):
            # 4xx means no rules (allow all). Some gateways answer 200 with an HTML error page.
            parser.parse([])
        else:
            parser.parse(response.text.splitlines())
        return parser

    # ------------------------------------------------------------------ storage

    def _path_for(self, url: str) -> Path:
        host = urlsplit(url).hostname or "unknown"
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
        return self._raw_dir / host / digest

    @staticmethod
    def _meta_path(path: Path) -> Path:
        return path.with_suffix(".meta.json")

    async def _read_meta(self, path: Path) -> _Meta | None:
        meta_path = anyio.Path(self._meta_path(path))
        if not await meta_path.exists():
            return None
        try:
            return _Meta(**json.loads(await meta_path.read_text("utf-8")))
        except (ValueError, TypeError):
            return None  # unreadable sidecar: fetch afresh

    async def _store(self, document: FetchedDocument, body: bytes, headers: httpx.Headers) -> None:
        meta = _Meta(
            url=document.url,
            final_url=document.final_url,
            kind=document.kind,
            content_type=document.content_type,
            sha256=document.sha256,
            fetched_at=document.fetched_at.isoformat(),
            etag=headers.get("etag"),
            last_modified=headers.get("last-modified"),
        )
        await anyio.Path(document.path.parent).mkdir(parents=True, exist_ok=True)
        await anyio.Path(document.path).write_bytes(body)
        await anyio.Path(self._meta_path(document.path)).write_text(
            json.dumps(asdict(meta), indent=2, ensure_ascii=False), "utf-8"
        )

    async def _reuse(self, path: Path, meta: _Meta) -> FetchedDocument:
        fetched_at = datetime.now(UTC)  # the source was verified unchanged just now
        meta.fetched_at = fetched_at.isoformat()
        await anyio.Path(self._meta_path(path)).write_text(
            json.dumps(asdict(meta), indent=2, ensure_ascii=False), "utf-8"
        )
        log.info("fetch_not_modified", url=meta.url)
        return FetchedDocument(
            url=meta.url,
            final_url=meta.final_url,
            kind=meta.kind,
            content_type=meta.content_type,
            sha256=meta.sha256,
            fetched_at=fetched_at,
            path=path.with_suffix(f".{meta.kind}"),
            not_modified=True,
        )

    async def _read_limited(self, response: httpx.Response, url: str) -> bytes:
        chunks: list[bytes] = []
        size = 0
        async for chunk in response.aiter_bytes():
            size += len(chunk)
            if size > self._max_bytes:
                raise FetchError(f"{url} is larger than {self._max_bytes} bytes")
            chunks.append(chunk)
        return b"".join(chunks)


def _kind(content_type: str, body: bytes, url: str) -> DocumentKind:
    if body.startswith(b"%PDF") or "application/pdf" in content_type:
        return "pdf"
    if "html" in content_type or body.lstrip()[:15].lower().startswith((b"<!doctype", b"<html")):
        return "html"
    raise FetchError(f"unsupported content type {content_type!r} for {url}")
