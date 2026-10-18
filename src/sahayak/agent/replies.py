"""Fixed replies in Hindi, Marathi and English (`replies.yaml`), and value formatting for them."""

from datetime import date, datetime
from functools import cache
from importlib import resources
from typing import Any

import yaml

from sahayak.agent.state import FieldValue
from sahayak.eligibility.profile import Language

LANGUAGES: tuple[Language, ...] = ("en", "hi", "mr")
LANGUAGE_NAMES: dict[Language, str] = {"en": "English", "hi": "Hindi", "mr": "Marathi"}

type Texts = dict[str, dict[Language, str]]


@cache
def load_replies() -> dict[str, Texts]:
    """`{"questions": {field: {lang: text}}, "messages": {key: {lang: text}}}`."""
    raw: dict[str, Any] = yaml.safe_load(
        resources.files("sahayak.agent").joinpath("replies.yaml").read_text("utf-8")
    )
    return {section: dict(entries) for section, entries in raw.items()}


def question(field: str, language: Language) -> str:
    """The fixed wording of the question for a profile field."""
    return load_replies()["questions"][field][language]


def message(key: str, language: Language, **values: str) -> str:
    return load_replies()["messages"][key][language].format(**values)


def format_value(value: FieldValue, language: Language) -> str:
    """A profile value as a person would read it."""
    if isinstance(value, bool):
        return message("answer_yes" if value else "answer_no", language)
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, str):
        return value.replace("_", " ")
    return str(value)


def format_date(value: date | datetime | None) -> str:
    """Day-month-year, as dates are usually written in India."""
    return "" if value is None else value.strftime("%d-%m-%Y")
