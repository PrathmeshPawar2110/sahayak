"""Translates queries with the configured LLM for real. Run with `uv run pytest -m live`."""

import pytest

from sahayak.config import Settings
from sahayak.db.models import Language
from sahayak.llm.factory import LLMConfigError, create_llm_provider
from sahayak.rag.translation import LLMQueryTranslator

pytestmark = pytest.mark.live


@pytest.mark.parametrize(
    ("query", "language", "words"),
    [
        ("मेरे पास दो एकड़ ज़मीन है, किसानों को सालाना पैसा मिलता है क्या?", Language.HI, {"farmer"}),
        ("माझ्याकडे दोन एकर शेती आहे, मला कोणत्या योजना मिळतील?", Language.MR, {"acre"}),
        ("mujhe budhape mein pension chahiye", Language.HI, {"pension"}),
    ],
)
async def test_translates_hindi_marathi_and_hinglish(
    query: str, language: Language, words: set[str]
) -> None:
    try:
        llm = create_llm_provider(Settings())
    except LLMConfigError as exc:
        pytest.skip(str(exc))

    english = await LLMQueryTranslator(llm).to_english(query, language)

    assert english is not None
    assert english.isascii()
    assert all(word in english.lower() for word in words)
