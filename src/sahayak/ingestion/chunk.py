"""Section-aware chunking (TRD §8 step 3): ~500 tokens with ~80 tokens of overlap.

Chunks never cross a section boundary, so every chunk has exactly one section for citations.
Inside a section the text is packed by paragraph, then sentence, then word, so a chunk only
breaks mid-sentence when a single sentence is longer than the chunk size.
"""

import hashlib
import math
import re
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace

from sahayak.db.models import Language
from sahayak.ingestion.parse import Section

CHUNK_TOKENS = 500
OVERLAP_TOKENS = 80
# Namespace for deterministic chunk ids: uuid5(namespace, "<scheme_id>:<content_hash>").
CHUNK_NAMESPACE = uuid.UUID("5a4a1f6e-8a37-4c38-9b8e-2f1f6c3d9a10")

# Words include Devanagari vowel signs and virama, which \w alone does not match.
_WORD_RE = re.compile(r"(?:\w|[\u0900-\u0963\u0966-\u097f])+|[^\w\s]", re.UNICODE)
# Sentence ends: Latin punctuation and the Devanagari danda (।) / double danda (॥).
_SENTENCE_END_RE = re.compile(r"(?<=[.!?\u0964\u0965])\s+")
_PARAGRAPH_RE = re.compile(r"\n\s*\n")

TokenCounter = Callable[[str], int]


def estimate_tokens(text: str) -> int:
    """Conservative estimate of multilingual-e5 (XLM-R) tokens without loading the tokenizer.

    Each word counts ceil(len / 3.5) and each punctuation mark 1. On the M1 sources this never
    under-counts (it over-counts by ~40% on typical text), so chunks stay inside e5's 512-token
    input (D-017).
    """
    return sum(
        max(1, math.ceil(len(token) / 3.5)) if token[0].isalnum() or token[0] == "_" else 1
        for token in _WORD_RE.findall(text)
    )


@dataclass(frozen=True)
class Chunk:
    scheme_id: str
    source_url: str
    section: str
    language: Language
    chunk_index: int
    text: str
    token_count: int
    content_hash: str

    @property
    def id(self) -> uuid.UUID:
        return chunk_id(self.scheme_id, self.content_hash)


def chunk_id(scheme_id: str, content_hash: str) -> uuid.UUID:
    return uuid.uuid5(CHUNK_NAMESPACE, f"{scheme_id}:{content_hash}")


def content_hash(source_url: str, section: str, language: Language, text: str) -> str:
    payload = "\x1f".join((source_url, section, language.value, text))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def chunk_sections(
    sections: Iterable[Section],
    *,
    scheme_id: str,
    source_url: str,
    language: Language,
    max_tokens: int = CHUNK_TOKENS,
    overlap_tokens: int = OVERLAP_TOKENS,
    count_tokens: TokenCounter = estimate_tokens,
) -> list[Chunk]:
    if overlap_tokens >= max_tokens:
        raise ValueError("overlap must be smaller than the chunk size")
    chunks: list[Chunk] = []
    for section in sections:
        for text in _pack(section.text, max_tokens, overlap_tokens, count_tokens):
            chunks.append(
                Chunk(
                    scheme_id=scheme_id,
                    source_url=source_url,
                    section=section.heading,
                    language=language,
                    chunk_index=len(chunks),
                    text=text,
                    token_count=count_tokens(text),
                    content_hash=content_hash(source_url, section.heading, language, text),
                )
            )
    return _deduplicate(chunks)


def _pack(text: str, max_tokens: int, overlap: int, count: TokenCounter) -> list[str]:
    units = list(_units(text, max_tokens, count))
    chunks: list[str] = []
    current: list[tuple[str, int]] = []  # (unit text, tokens)
    size = 0
    for unit, tokens in units:
        if current and size + tokens > max_tokens:
            chunks.append(_join(current))
            current = _tail(current, overlap, count)
            size = sum(t for _, t in current)
            # The overlap plus the next unit may still not fit: drop overlap rather than overflow.
            while current and size + tokens > max_tokens:
                size -= current.pop(0)[1]
        current.append((unit, tokens))
        size += tokens
    if current:
        chunks.append(_join(current))
    return chunks


def _units(text: str, max_tokens: int, count: TokenCounter) -> Iterable[tuple[str, int]]:
    """Paragraphs, split into sentences, split into word runs, until each fits in a chunk."""
    for paragraph in _PARAGRAPH_RE.split(text):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        tokens = count(paragraph)
        if tokens <= max_tokens:
            yield paragraph, tokens
            continue
        for sentence in _SENTENCE_END_RE.split(paragraph):
            sentence = sentence.strip()
            if not sentence:
                continue
            tokens = count(sentence)
            if tokens <= max_tokens:
                yield sentence, tokens
            else:
                yield from _word_runs(sentence, max_tokens, count)


def _word_runs(sentence: str, max_tokens: int, count: TokenCounter) -> Iterable[tuple[str, int]]:
    words: list[str] = []
    size = 0
    for word in sentence.split():
        tokens = count(word)
        if words and size + tokens > max_tokens:
            yield " ".join(words), size
            words, size = [], 0
        words.append(word)
        size += tokens
    if words:
        yield " ".join(words), size


def _tail(units: list[tuple[str, int]], overlap: int, count: TokenCounter) -> list[tuple[str, int]]:
    """The last units of a chunk worth at most `overlap` tokens, trimming a long unit by words."""
    tail: list[tuple[str, int]] = []
    size = 0
    for unit, tokens in reversed(units):
        if size + tokens <= overlap:
            tail.insert(0, (unit, tokens))
            size += tokens
            continue
        # Take the end of this unit word by word to fill the remaining overlap budget.
        words: list[str] = []
        for word in reversed(unit.split()):
            word_tokens = count(word)
            if size + word_tokens > overlap:
                break
            words.insert(0, word)
            size += word_tokens
        if words:
            piece = " ".join(words)
            tail.insert(0, (piece, count(piece)))
        break
    return tail


def _join(units: list[tuple[str, int]]) -> str:
    return "\n\n".join(text for text, _ in units)


def _deduplicate(chunks: list[Chunk]) -> list[Chunk]:
    """Identical text in the same section of one source (e.g. a repeated box) is indexed once."""
    seen: set[str] = set()
    unique: list[Chunk] = []
    for chunk in chunks:
        if chunk.content_hash in seen:
            continue
        seen.add(chunk.content_hash)
        unique.append(replace(chunk, chunk_index=len(unique)))  # keep indexes contiguous
    return unique
