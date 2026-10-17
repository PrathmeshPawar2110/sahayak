import itertools
import re

import pytest

from sahayak.db.models import Language
from sahayak.ingestion.chunk import (
    Chunk,
    chunk_id,
    chunk_sections,
    estimate_tokens,
)
from sahayak.ingestion.parse import Section

URL = "https://scheme.gov.in/guidelines.pdf"


def word_count(text: str) -> int:
    """A simple counter for tests: one token per whitespace-separated word."""
    return len(text.split())


def chunks_of(*sections: Section, **kwargs: object) -> list[Chunk]:
    options: dict[str, object] = {
        "scheme_id": "pm-kisan",
        "source_url": URL,
        "language": Language.EN,
        "count_tokens": word_count,
    }
    options.update(kwargs)
    return chunk_sections(list(sections), **options)  # type: ignore[arg-type]


def sentences(prefix: str, n: int, words_each: int = 10) -> str:
    return " ".join(
        f"{prefix}{i} " + " ".join(f"w{i}x{j}" for j in range(words_each - 2)) + " end."
        for i in range(n)
    )


def test_short_section_is_one_chunk_with_metadata() -> None:
    [chunk] = chunks_of(Section("Exclusions", "Income tax payers are excluded."))

    assert chunk.section == "Exclusions"
    assert chunk.scheme_id == "pm-kisan"
    assert chunk.source_url == URL
    assert chunk.language is Language.EN
    assert chunk.chunk_index == 0
    assert chunk.token_count == 5
    assert chunk.id == chunk_id("pm-kisan", chunk.content_hash)


def test_long_sections_are_split_within_the_size_limit() -> None:
    text = sentences("s", 30)  # 300 words

    chunks = chunks_of(Section("Benefits", text), max_tokens=50, overlap_tokens=10)

    assert len(chunks) > 5
    assert all(c.token_count <= 50 for c in chunks)
    assert [c.chunk_index for c in chunks] == list(range(len(chunks)))


def test_consecutive_chunks_overlap() -> None:
    chunks = chunks_of(Section("Benefits", sentences("s", 30)), max_tokens=50, overlap_tokens=10)

    for previous, current in itertools.pairwise(chunks):
        head = current.text.split()[:5]
        assert " ".join(head) in previous.text


def test_every_word_survives_chunking() -> None:
    text = sentences("s", 40)

    chunks = chunks_of(Section("Benefits", text), max_tokens=60, overlap_tokens=15)

    seen = {word for c in chunks for word in c.text.split()}
    assert set(text.split()) <= seen


def test_chunks_never_cross_sections() -> None:
    chunks = chunks_of(
        Section("Eligibility", sentences("e", 3)),
        Section("Exclusions", sentences("x", 3)),
        max_tokens=500,
    )

    assert [c.section for c in chunks] == ["Eligibility", "Exclusions"]
    assert not any(word.startswith("x") for word in chunks[0].text.split())


def test_sentence_without_punctuation_is_split_into_word_runs() -> None:
    long_sentence = " ".join(f"word{i}" for i in range(120))

    chunks = chunks_of(Section("Table", long_sentence), max_tokens=50, overlap_tokens=5)

    assert all(c.token_count <= 50 for c in chunks)
    assert chunks[0].text.startswith("word0 word1")


def test_devanagari_danda_ends_sentences() -> None:
    hindi = " ".join(f"वाक्य{i} " + "किसान " * 8 + "है।" for i in range(12))

    chunks = chunks_of(
        Section("पात्रता", hindi), language=Language.HI, max_tokens=25, overlap_tokens=0
    )

    assert all(c.text.endswith("है।") for c in chunks)


def test_paragraphs_are_kept_together_when_they_fit() -> None:
    text = "First paragraph about land.\n\nSecond paragraph about income."

    [chunk] = chunks_of(Section("Overview", text))

    assert chunk.text == text


def test_identical_text_in_one_section_is_indexed_once() -> None:
    chunks = chunks_of(Section("Note", "Apply online."), Section("Note", "Apply online."))

    assert len(chunks) == 1


def test_content_hash_depends_on_source_section_and_language() -> None:
    [base] = chunks_of(Section("A", "same text"))
    [other_url] = chunks_of(Section("A", "same text"), source_url="https://other.gov.in")
    [other_section] = chunks_of(Section("B", "same text"))
    [other_lang] = chunks_of(Section("A", "same text"), language=Language.HI)

    hashes = {c.content_hash for c in (base, other_url, other_section, other_lang)}
    assert len(hashes) == 4


def test_chunk_ids_are_stable_across_runs() -> None:
    first = chunks_of(Section("A", sentences("s", 20)), max_tokens=40, overlap_tokens=10)
    second = chunks_of(Section("A", sentences("s", 20)), max_tokens=40, overlap_tokens=10)

    assert [c.id for c in first] == [c.id for c in second]


def test_overlap_must_be_smaller_than_chunk() -> None:
    with pytest.raises(ValueError, match="overlap"):
        chunks_of(Section("A", "text"), max_tokens=50, overlap_tokens=50)


def test_default_settings_use_the_e5_estimate() -> None:
    text = sentences("s", 200)

    chunks = chunk_sections(
        [Section("Long", text)], scheme_id="x", source_url=URL, language=Language.EN
    )

    assert all(c.token_count <= 500 for c in chunks)
    assert all(c.token_count == estimate_tokens(c.text) for c in chunks)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("", 0),
        ("the farmer", 1 + 2),  # ceil(3/3.5)=1, ceil(6/3.5)=2
        ("₹6,000", 1 + 1 + 1 + 1),  # ₹ , and two numbers
        ("किसान", 2),
    ],
)
def test_estimate_tokens(text: str, expected: int) -> None:
    assert estimate_tokens(text) == expected


def test_estimate_counts_every_word() -> None:
    text = "Eligibility: landholding farmer families."

    assert estimate_tokens(text) >= len(re.findall(r"\w+", text))
