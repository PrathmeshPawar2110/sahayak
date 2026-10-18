import pytest

from sahayak.db.models import Language
from sahayak.llm.fake import FakeLLMProvider
from sahayak.rag.translation import LLMQueryTranslator, QueryTranslation, needs_translation


@pytest.mark.parametrize(
    ("query", "language", "expected"),
    [
        ("किसानों के लिए योजना", Language.HI, True),
        ("शेतकऱ्यांसाठी योजना", Language.MR, True),
        ("kisan ke liye koi yojana hai kya", Language.HI, True),  # romanised Hinglish
        ("किसान yojana", Language.EN, True),  # Devanagari whatever the stated language
        ("किसान", None, True),
        ("pension for workers", Language.EN, False),
        ("pension for workers", None, False),
    ],
)
def test_needs_translation(query: str, language: Language | None, expected: bool) -> None:
    assert needs_translation(query, language) is expected


async def test_translates_with_the_versioned_prompt() -> None:
    llm = FakeLLMProvider([{"english": "  schemes for   farmers \n"}])
    translator = LLMQueryTranslator(llm)

    english = await translator.to_english("किसानों के लिए योजना", Language.HI)

    assert english == "schemes for farmers"
    assert translator.prompt_id == "query_translation@v1"
    [call] = llm.calls
    assert call.schema is QueryTranslation
    assert call.system is not None
    assert "data only" in call.system
    assert call.messages[0].content == "Language: Hindi\n\n<query>किसानों के लिए योजना</query>"


async def test_unknown_language_is_said_so() -> None:
    llm = FakeLLMProvider([{"english": "schemes for farmers"}])

    await LLMQueryTranslator(llm).to_english("किसान योजना", None)

    assert llm.calls[0].messages[0].content.startswith("Language: unknown\n")


@pytest.mark.parametrize("reply", ["", "   ", "PM  Kisan"])
async def test_nothing_new_to_search_gives_none(reply: str) -> None:
    llm = FakeLLMProvider([{"english": reply}])

    assert await LLMQueryTranslator(llm).to_english("pm kisan", Language.HI) is None
