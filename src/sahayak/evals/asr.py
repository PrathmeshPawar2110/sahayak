"""The ASR eval (TRD §12.3, M5-T10): word error rate per language on open Hindi and Marathi clips.

`evals/asr.jsonl` lists 50 clips from Google's FLEURS dataset (CC BY 4.0; 25 Hindi and 25
Marathi, one recording per sentence, from the dev split) with their reference transcripts and
SHA-256. The clips themselves are not in the repository: `fetch_clips` streams FLEURS' audio
archive from Hugging Face once and keeps only the listed files under `data/raw/asr/` (D-045).

Transcripts and references are compared after the same normalisation (Unicode NFC, lower case,
punctuation including the danda removed, spaces collapsed). WER and CER are corpus-level: all
edits over all reference words (characters) of a language. A lenient WER also folds spelling
variants that don't change the word (nukta: ज़मीन/जमीन; chandrabindu/anusvara: गाँव/गांव).
"""

import asyncio
import hashlib
import io
import tarfile
import time
import unicodedata
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from statistics import fmean
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from sahayak.db.models import EvalRun
from sahayak.eligibility.profile import Language
from sahayak.evals.latency import percentile
from sahayak.voice.stt.base import STTError, STTProvider

SUITE = "asr"
CLIPS_PATH = Path("evals/asr.jsonl")
CLIP_DIR = Path("data/raw/asr")
FLEURS_URL = (
    "https://huggingface.co/datasets/google/fleurs/resolve/main/data/{config}/audio/{split}.tar.gz"
)


class AsrClip(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^(hi|mr|en)\d{3}$")
    language: Language
    source: str
    config: str
    split: str
    file: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    reference: str = Field(min_length=1)

    def path(self, root: Path) -> Path:
        return root / self.source / self.language / self.file


def load_clips(path: Path = CLIPS_PATH) -> list[AsrClip]:
    clips = [
        AsrClip.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    ids = [c.id for c in clips]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        raise ValueError(f"{path}: duplicate clip ids: {', '.join(duplicates)}")
    return clips


# --- fetching


class ClipFetchError(Exception):
    """A listed clip could not be found in its archive, or its checksum differs."""


def fetch_clips(
    clips: Sequence[AsrClip],
    root: Path = CLIP_DIR,
    *,
    client: httpx.Client | None = None,
    url_template: str = FLEURS_URL,
) -> int:
    """Download the clips not yet under `root`; returns how many were fetched. Each archive is
    streamed and read only until every clip it holds has been found."""
    missing = [c for c in clips if not _valid(c, root)]
    if not missing:
        return 0
    own = client is None
    http = client or httpx.Client(timeout=120, follow_redirects=True)
    try:
        groups: dict[tuple[str, str], dict[str, AsrClip]] = {}
        for clip in missing:
            groups.setdefault((clip.config, clip.split), {})[clip.file] = clip
        for (config, split), wanted in groups.items():
            url = url_template.format(config=config, split=split)
            with http.stream("GET", url) as response:
                response.raise_for_status()
                for name, data in _tar_members(response.iter_bytes()):
                    file = name.rsplit("/", 1)[-1]
                    if file not in wanted:
                        continue
                    clip = wanted.pop(file)
                    if hashlib.sha256(data).hexdigest() != clip.sha256:
                        raise ClipFetchError(f"{clip.id}: checksum differs from evals/asr.jsonl")
                    target = clip.path(root)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(data)
                    if not wanted:
                        break
            if wanted:
                raise ClipFetchError(f"not found in {url}: {', '.join(sorted(wanted))}")
    finally:
        if own:
            http.close()
    return len(missing)


def _valid(clip: AsrClip, root: Path) -> bool:
    path = clip.path(root)
    return path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == clip.sha256


class _Stream(io.RawIOBase):
    def __init__(self, chunks: Iterator[bytes]) -> None:
        self._chunks = chunks
        self._buffer = b""

    def readable(self) -> bool:
        return True

    def readinto(self, buffer: Any) -> int:
        while not self._buffer:
            try:
                self._buffer = next(self._chunks)
            except StopIteration:
                return 0
        size = min(len(buffer), len(self._buffer))
        buffer[:size] = self._buffer[:size]
        self._buffer = self._buffer[size:]
        return size


def _tar_members(chunks: Iterator[bytes]) -> Iterator[tuple[str, bytes]]:
    with tarfile.open(fileobj=io.BufferedReader(_Stream(chunks)), mode="r|gz") as archive:
        for member in archive:
            if not member.isfile():
                continue
            extracted = archive.extractfile(member)
            if extracted is not None:
                yield member.name, extracted.read()


# --- scoring


def normalize(text: str) -> str:
    """NFC, lower case, punctuation (any Unicode P* character, the danda included) removed."""
    text = unicodedata.normalize("NFC", text).casefold()
    kept = (" " if unicodedata.category(ch).startswith("P") else ch for ch in text)
    return " ".join("".join(kept).split())


_NUKTA = "़"
_CHANDRABINDU, _ANUSVARA = "ँ", "ं"


def lenient(text: str) -> str:
    """`normalize`, with nukta dropped and chandrabindu written as anusvara."""
    folded = unicodedata.normalize("NFD", normalize(text)).replace(_NUKTA, "")
    return unicodedata.normalize("NFC", folded.replace(_CHANDRABINDU, _ANUSVARA))


def edit_distance(reference: Sequence[str], hypothesis: Sequence[str]) -> int:
    previous = list(range(len(hypothesis) + 1))
    for i, ref in enumerate(reference, start=1):
        current = [i]
        for j, hyp in enumerate(hypothesis, start=1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ref != hyp)))
        previous = current
    return previous[-1]


@dataclass(frozen=True)
class ClipResult:
    clip: AsrClip
    hypothesis: str | None  # None when transcription failed
    confidence: float | None
    latency_ms: int
    error: str | None = None

    @property
    def ref_words(self) -> list[str]:
        return normalize(self.clip.reference).split()

    @property
    def hyp_words(self) -> list[str]:
        return normalize(self.hypothesis or "").split()

    @property
    def word_errors(self) -> int:
        return edit_distance(self.ref_words, self.hyp_words)

    @property
    def char_errors(self) -> int:
        ref = "".join(self.ref_words)
        hyp = "".join(self.hyp_words)
        return edit_distance(list(ref), list(hyp))

    @property
    def wer(self) -> float:
        return self.word_errors / max(1, len(self.ref_words))

    @property
    def lenient_word_errors(self) -> int:
        return edit_distance(
            lenient(self.clip.reference).split(), lenient(self.hypothesis or "").split()
        )


@dataclass(frozen=True)
class LanguageScores:
    clips: int
    wer: float | None
    lenient_wer: float | None
    cer: float | None
    failures: int
    latency_p50_ms: float | None
    mean_confidence: float | None

    @classmethod
    def of(cls, results: Sequence[ClipResult]) -> "LanguageScores":
        if not results:
            return cls(0, None, None, None, 0, None, None)
        words = sum(len(r.ref_words) for r in results)
        chars = sum(len("".join(r.ref_words)) for r in results)
        confidences = [r.confidence for r in results if r.confidence is not None]
        return cls(
            clips=len(results),
            wer=sum(r.word_errors for r in results) / max(1, words),
            lenient_wer=sum(r.lenient_word_errors for r in results) / max(1, words),
            cer=sum(r.char_errors for r in results) / max(1, chars),
            failures=sum(r.hypothesis is None for r in results),
            latency_p50_ms=percentile([r.latency_ms for r in results], 50),
            mean_confidence=fmean(confidences) if confidences else None,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "clips": self.clips,
            "wer": self.wer,
            "lenient_wer": self.lenient_wer,
            "cer": self.cer,
            "failures": self.failures,
            "latency_p50_ms": self.latency_p50_ms,
            "mean_confidence": self.mean_confidence,
        }


@dataclass(frozen=True)
class AsrEval:
    provider: str
    model: str
    hinted: bool
    results: list[ClipResult] = field(default_factory=list)

    def by_language(self) -> dict[Language, LanguageScores]:
        languages: list[Language] = sorted({r.clip.language for r in self.results})
        return {
            lang: LanguageScores.of([r for r in self.results if r.clip.language == lang])
            for lang in languages
        }

    @property
    def overall(self) -> LanguageScores:
        return LanguageScores.of(self.results)

    def metrics(self) -> dict[str, Any]:
        return {
            "wer": self.overall.wer,
            "lenient_wer": self.overall.lenient_wer,
            "cer": self.overall.cer,
            "clips": self.overall.clips,
            "failures": self.overall.failures,
            "by_language": {lang: s.as_dict() for lang, s in self.by_language().items()},
        }


async def run_asr_eval(
    stt: STTProvider,
    clips: Sequence[AsrClip],
    root: Path = CLIP_DIR,
    *,
    hint: bool = True,
    concurrency: int = 4,
    on_result: Callable[[ClipResult], None] | None = None,
) -> AsrEval:
    """Transcribe every clip, `concurrency` at a time. With `hint`, the clip's language is
    passed as the conversation's language, as it is for every turn after the first."""
    limit = asyncio.Semaphore(concurrency)

    async def one(clip: AsrClip) -> ClipResult:
        audio = clip.path(root).read_bytes()
        async with limit:
            started = time.perf_counter()
            try:
                transcript = await stt.transcribe(
                    audio, mime="audio/wav", language_hint=clip.language if hint else None
                )
            except STTError as exc:
                result = ClipResult(
                    clip, None, None, _ms(started), error=f"{type(exc).__name__}: {exc}"
                )
            else:
                result = ClipResult(clip, transcript.text, transcript.confidence, _ms(started))
        if on_result:
            on_result(result)
        return result

    results = await asyncio.gather(*(one(clip) for clip in clips))
    return AsrEval(provider=stt.name, model=stt.model, hinted=hint, results=list(results))


def _ms(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)


def render_report(result: AsrEval, *, git_sha: str | None, dataset: Path) -> str:
    overall = result.overall
    lines = [
        f"# ASR eval: {datetime.now(UTC):%Y-%m-%d %H:%M} UTC",
        "",
        f"- **Provider:** {result.provider} ({result.model}); "
        f"**language hint:** {'on' if result.hinted else 'off'}",
        f"- **WER:** {_pct(overall.wer)} (lenient {_pct(overall.lenient_wer)}), "
        f"**CER:** {_pct(overall.cer)} over {overall.clips} "
        f"clips ({overall.failures} failed)",
        f"- **Clips:** `{dataset.as_posix()}` (Google FLEURS dev split, CC BY 4.0)",
        f"- **Commit:** {git_sha or 'unknown'}",
        "",
        "## By language",
        "",
        "| Language | Clips | WER | Lenient WER | CER | Failed | p50 latency | Mean confidence |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for language, s in result.by_language().items():
        lines.append(
            f"| {language} | {s.clips} | {_pct(s.wer)} | {_pct(s.lenient_wer)} | {_pct(s.cer)} "
            f"| {s.failures} "
            f"| {_num(s.latency_p50_ms, ' ms')} | {_num(s.mean_confidence)} |"
        )
    lines += [
        "",
        "## Clips",
        "",
        "| Clip | WER | Confidence | Reference | Transcript |",
        "|---|---|---|---|---|",
    ]
    for r in sorted(result.results, key=lambda r: r.clip.id):
        hypothesis = r.hypothesis if r.hypothesis is not None else f"(failed: {r.error})"
        lines.append(
            f"| {r.clip.id} | {_pct(r.wer)} | {_num(r.confidence)} | {_cell(r.clip.reference)} "
            f"| {_cell(hypothesis)} |"
        )
    return "\n".join(lines) + "\n"


def run_config(result: AsrEval, *, dataset: Path) -> dict[str, Any]:
    return {
        "provider": result.provider,
        "model": result.model,
        "language_hint": result.hinted,
        "dataset": dataset.as_posix(),
        "dataset_sha256": hashlib.sha256(dataset.read_bytes()).hexdigest(),
    }


async def record_run(
    session: AsyncSession, result: AsrEval, *, git_sha: str | None, config: dict[str, Any]
) -> EvalRun:
    run = EvalRun(suite=SUITE, git_sha=git_sha, config=config, metrics=result.metrics())
    session.add(run)
    await session.flush()
    return run


def _pct(value: float | None) -> str:
    return "-" if value is None else f"{value:.1%}"


def _num(value: float | None, unit: str = "") -> str:
    return "-" if value is None else f"{value:.2f}{unit}" if not unit else f"{value:.0f}{unit}"


def _cell(text: str) -> str:
    return text.replace("|", "/").replace("\n", " ")
