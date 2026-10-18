"""English translations of user queries, for searching English scheme text (TRD §8, M3-T04).

Most scheme sources are English-only (D-012). multilingual-e5 puts Hindi, Marathi and English
in one vector space, but full-text search only matches shared words, so a Hindi or Marathi query
finds English chunks through its translation.
"""

import re
from abc import ABC, abstractmethod

from pydantic import BaseModel, ConfigDict, Field

from sahayak.db.models import Language
from sahayak.llm.base import ChatMessage, LLMProvider
from sahayak.llm.prompts import load_prompt

PROMPT_NAME = "query_translation"
LANGUAGE_NAMES = {Language.HI: "Hindi", Language.MR: "Marathi", Language.EN: "English"}
_DEVANAGARI_RE = re.compile(r"[ऀ-ॿ]")


def needs_translation(query: str, language: Language | None) -> bool:
    """A query is translated when the user speaks Hindi or Marathi (including romanised
    Hinglish), or when it contains Devanagari whatever the stated language."""
    return language in (Language.HI, Language.MR) or bool(_DEVANAGARI_RE.search(query))


class QueryTranslator(ABC):
    @abstractmethod
    async def to_english(self, query: str, language: Language | None) -> str | None:
        """The query in English, or None when there's nothing new to search for."""


class QueryTranslation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    english: str = Field(description="The citizen's query in English.")


class LLMQueryTranslator(QueryTranslator):
    """Translates with the configured LLM and the versioned `query_translation` prompt."""

    def __init__(self, llm: LLMProvider, *, max_tokens: int = 200) -> None:
        self._llm = llm
        self._max_tokens = max_tokens
        self.prompt_id = load_prompt(PROMPT_NAME).id

    async def to_english(self, query: str, language: Language | None) -> str | None:
        prompt = load_prompt(PROMPT_NAME).render(
            language=LANGUAGE_NAMES[language] if language else "unknown",
            query=query,
        )
        response = await self._llm.structured(
            [ChatMessage("user", prompt.user)],
            QueryTranslation,
            system=prompt.system,
            max_tokens=self._max_tokens,
        )
        english = " ".join(response.value.english.split())
        if not english or english.casefold() == " ".join(query.split()).casefold():
            return None
        return english
