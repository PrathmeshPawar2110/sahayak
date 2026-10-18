"""Speaking replies: what is said, sentence by sentence, with a Redis cache (TRD §5 Cost, FR-10).

- `spoken_text` turns a reply as displayed into what should be said: no citation markers, no
  sources list, no URLs read out character by character.
- `Speaker` splits text into sentences and synthesises each one once: sentences are cached in
  Redis as MP3 under (voice, language, text hash), so fixed wording (questions, notices,
  caveats) is synthesised once for everyone. `speak` joins the sentences into one file in the
  format a channel needs; `stream` yields them one by one as they are ready, so the first audio
  plays before the whole reply is synthesised.

The cache fails open: when Redis is unreachable, sentences are synthesised as if nothing were
cached, and Redis is skipped for `retry_after_sec`. Audio from a fallback provider is not cached,
so the main voice comes back as soon as its provider does.
"""

import asyncio
import hashlib
import re
import time
from collections.abc import AsyncIterator, Callable, Sequence

import structlog
from redis.asyncio import Redis
from redis.exceptions import RedisError

from sahayak.agent.replies import message
from sahayak.eligibility.profile import Language
from sahayak.voice import audio
from sahayak.voice.audio import Audio, AudioError, AudioFormat
from sahayak.voice.tts.base import TTSError, TTSProvider

log = structlog.get_logger(__name__)

KEY_VERSION = "v1"
# Sentences shorter than this are spoken together with the next one.
MIN_SENTENCE_CHARS = 25

_SENTENCE_END = re.compile(r"(?<=[.?!।॥])\s+|\n+")
_CITATION = re.compile(r"\s*\[\d+\]")
_URL = re.compile(r"https?://\S+?(?=[.,;:)]*(?:\s|$))")
_SYMBOLS = re.compile(r"[•⚠️*#]")


def spoken_text(text: str, language: Language) -> str:
    """What to say for a reply: the reply without citation markers, sources or URLs."""
    heading = f"{message('sources', language)}:"
    paragraphs = [p for p in text.split("\n\n") if not p.strip().startswith(heading)]
    spoken = _CITATION.sub("", "\n".join(paragraphs))
    spoken = _URL.sub(message("official_website", language), spoken)
    spoken = _SYMBOLS.sub("", spoken)
    lines = (" ".join(line.split()) for line in spoken.splitlines())
    return "\n".join(line for line in lines if line)


def sentences(text: str, max_chars: int) -> list[str]:
    """Split text into sentences (short ones joined to the next), none longer than `max_chars`."""
    pieces: list[str] = []
    carry = ""
    for piece in (p.strip() for p in _SENTENCE_END.split(text)):
        if not piece:
            continue
        piece = f"{carry} {piece}".strip() if carry else piece
        if len(piece) < MIN_SENTENCE_CHARS:
            carry = piece
            continue
        carry = ""
        pieces.extend(_wrap(piece, max_chars))
    if carry:
        if pieces and len(pieces[-1]) + len(carry) < max_chars:
            pieces[-1] = f"{pieces[-1]} {carry}"
        else:
            pieces.extend(_wrap(carry, max_chars))
    return pieces


def _wrap(text: str, max_chars: int) -> list[str]:
    if len(text) <= max_chars:
        return [text]
    lines: list[str] = []
    line = ""
    for word in text.split():
        while len(word) > max_chars:  # no spaces to break at
            if line:
                lines.append(line)
                line = ""
            lines.append(word[:max_chars])
            word = word[max_chars:]
        if line and len(line) + 1 + len(word) > max_chars:
            lines.append(line)
            line = word
        else:
            line = f"{line} {word}".strip()
    if line:
        lines.append(line)
    return lines


class Speaker:
    def __init__(
        self,
        tts: TTSProvider,
        redis: Redis | None,
        *,
        ttl_sec: int,
        concurrency: int = 3,
        retry_after_sec: float = 30.0,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._tts = tts
        self._redis = redis
        self._ttl_sec = ttl_sec
        self._limit = asyncio.Semaphore(concurrency)
        self._retry_after_sec = retry_after_sec
        self._monotonic = monotonic
        self._skip_until = 0.0

    @property
    def voice_id(self) -> str:
        return self._tts.voice_id

    async def aclose(self) -> None:
        await self._tts.aclose()

    def key(self, sentence: str, language: Language) -> str:
        digest = hashlib.sha256(sentence.encode("utf-8")).hexdigest()
        return f"tts:{KEY_VERSION}:{self._tts.voice_id}:{language}:{digest}"

    def split(self, text: str) -> list[str]:
        return sentences(text, self._tts.max_chars)

    async def speak(self, text: str, language: Language, to: AudioFormat) -> Audio:
        """The whole text as one `to` file."""
        parts = self.split(text)
        if not parts:
            raise TTSError("nothing to say")
        cached = await self._cached(parts, language)
        mp3s = await asyncio.gather(
            *(self._sentence(part, language, hit) for part, hit in zip(parts, cached, strict=True))
        )
        return Audio(await asyncio.to_thread(audio.join, mp3s, to), to)

    async def stream(self, text: str, language: Language, to: AudioFormat) -> AsyncIterator[Audio]:
        """The text sentence by sentence, in order, each as its own `to` file. Later sentences
        are synthesised while earlier ones are sent."""
        parts = self.split(text)
        if not parts:
            raise TTSError("nothing to say")
        cached = await self._cached(parts, language)
        tasks = [
            asyncio.create_task(self._sentence(part, language, hit))
            for part, hit in zip(parts, cached, strict=True)
        ]
        try:
            for task in tasks:
                mp3 = await task
                data = mp3 if to == "mp3" else await asyncio.to_thread(audio.encode, [mp3], to)
                yield Audio(data, to)
        finally:
            for task in tasks:
                task.cancel()

    async def forget(self, text: str, language: Language) -> None:
        """Delete the cached sentences of `text` (its audio may repeat what the user said)."""
        if self._redis is None or self._monotonic() < self._skip_until:
            return
        if not (parts := self.split(text)):
            return
        try:
            await self._redis.delete(*(self.key(part, language) for part in parts))
        except RedisError as exc:
            self._unavailable(exc)

    async def _sentence(self, sentence: str, language: Language, cached: bytes | None) -> bytes:
        if cached is not None:
            return cached
        async with self._limit:
            synthesis = await self._tts.synthesize(sentence, language)
        try:
            mp3 = await asyncio.to_thread(audio.encode, [synthesis.audio], "mp3")
        except AudioError as exc:
            raise TTSError(f"{self._tts.name} returned audio that could not be read") from exc
        if synthesis.voice_id == self._tts.voice_id:
            await self._store(self.key(sentence, language), mp3)
        return mp3

    async def _cached(self, parts: Sequence[str], language: Language) -> list[bytes | None]:
        if self._redis is None or self._monotonic() < self._skip_until:
            return [None] * len(parts)
        try:
            values: list[bytes | str | None] = await self._redis.mget(
                [self.key(part, language) for part in parts]
            )
        except RedisError as exc:
            self._unavailable(exc)
            return [None] * len(parts)
        hits = [value if isinstance(value, bytes) and value else None for value in values]
        log.info("tts_cache", sentences=len(parts), hits=sum(h is not None for h in hits))
        return hits

    async def _store(self, key: str, mp3: bytes) -> None:
        if self._redis is None or self._monotonic() < self._skip_until:
            return
        try:
            await self._redis.set(key, mp3, ex=self._ttl_sec)
        except RedisError as exc:
            self._unavailable(exc)

    def _unavailable(self, exc: Exception) -> None:
        log.warning("tts_cache_unavailable", error=type(exc).__name__)
        self._skip_until = self._monotonic() + self._retry_after_sec
