"""`extract_profile`: merge what the user stated into the profile (TRD §10, FR-4, FR-14).

The facts come from `classify_intent`'s structured LLM output. Here they are checked and merged
deterministically:

- Each value is parsed for its field's type and validated by `UserProfile` (ranges, choices,
  state codes). Invalid values are dropped, never guessed at.
- Sensitive fields are kept only with the session's sensitive-data consent (TRD §8B).
- A value that looks like an Aadhaar, bank account, OTP or phone number is never saved (TRD §10).
- A new value fills an unknown field and records a `FieldSource`. A value that disagrees with a
  known one does not overwrite it: it becomes a `Contradiction` the agent asks about. The
  answer to that confirmation question is taken as given; if the user doesn't answer it, the
  known value stays.
- Fields the user declines (or doesn't know) are remembered, so they aren't asked again.
- A voice message the speech-to-text was unsure about (confidence below
  `asr_confirm_threshold`) saves nothing new: each value it would add or change becomes an
  `unclear_audio` item, and the agent reads back what it heard. Values confirmed on the next
  turn are saved; unconfirmed ones are dropped (D-043).
"""

import structlog
from langgraph.runtime import Runtime
from pydantic import ValidationError

from sahayak.agent.conversation import latest_user_text
from sahayak.agent.deps import AgentDeps
from sahayak.agent.state import AgentState, Contradiction, ExtractedFact, FieldValue
from sahayak.eligibility.fields import PROFILE_FIELDS
from sahayak.eligibility.profile import FieldSource, UserProfile
from sahayak.privacy.pii import find_pii

log = structlog.get_logger(__name__)

_TRUE = {"true", "yes"}
_FALSE = {"false", "no"}
MAX_TEXT_LENGTH = 100


def parse_fact(fact: ExtractedFact) -> FieldValue | None:
    """The fact's value as its field's type, validated like `UserProfile` does, or None."""
    field = PROFILE_FIELDS[fact.field]
    text = fact.value.strip()
    value: FieldValue | None
    match field.kind:
        case "bool":
            lowered = text.casefold()
            value = True if lowered in _TRUE else False if lowered in _FALSE else None
        case "int" | "float":
            value = _number(text, whole=field.kind == "int")
        case "enum":
            value = text.casefold() if text.casefold() in field.choices else None
        case "str":
            value = text if 0 < len(text) <= MAX_TEXT_LENGTH else None
    if value is None:
        return None
    try:
        validated: FieldValue = field.adapter.validate_python(value)
    except ValidationError:
        return None
    return validated


def _number(text: str, *, whole: bool) -> int | float | None:
    cleaned = text.replace(",", "").replace("_", "").replace(" ", "").lstrip("₹")
    try:
        number = float(cleaned)
    except ValueError:
        return None
    if whole:
        return int(number) if number.is_integer() else None
    return number


def _confirming(state: AgentState) -> set[str]:
    """Fields the agent's last message asked the user to confirm."""
    pending = state.get("pending_question")
    if pending is None:
        return set()
    if pending.kind == "confirm":
        return {pending.field}
    if pending.kind == "confirm_heard":
        return {
            c.field
            for c in state.get("contradictions", [])
            if c.reason == "unclear_audio" and c.asked
        }
    return set()


def _unclear_audio(state: AgentState, threshold: float) -> bool:
    confidence = state.get("asr_confidence")
    return confidence is not None and confidence < threshold


MAX_HEARD_CHARS = 200


def _heard(state: AgentState) -> str:
    text = " ".join(latest_user_text(state).split())
    return text if len(text) <= MAX_HEARD_CHARS else f"{text[: MAX_HEARD_CHARS - 1]}…"


def same_value(a: FieldValue, b: FieldValue) -> bool:
    # bool is an int in Python: keep True from equalling 1.
    return isinstance(a, bool) is isinstance(b, bool) and a == b


async def extract_profile(state: AgentState, runtime: Runtime[AgentDeps]) -> AgentState:
    understanding = state.get("understanding")
    if understanding is None:
        return {}
    profile = state["profile"]
    confirming = _confirming(state)
    unclear = _unclear_audio(state, runtime.context.config.asr_confirm_threshold)
    asr_confidence = state.get("asr_confidence")
    # Typed text, or speech whose provider gives no confidence, counts as certain.
    confidence = 1.0 if asr_confidence is None else asr_confidence
    sensitive_ok = "sensitive" in state.get("consents", [])
    contradictions = {c.field: c for c in state.get("contradictions", [])}
    declined = list(state.get("declined_fields", []))
    sources = dict(profile.field_sources)
    updates: dict[str, FieldValue] = {}
    answered: set[str] = set()
    dropped = 0

    for fact in understanding.facts:
        field = PROFILE_FIELDS[fact.field]
        value = None if find_pii(fact.value) else parse_fact(fact)
        if value is None or (field.sensitive and not sensitive_ok):
            dropped += 1
            continue
        answered.add(field.name)
        current = updates.get(field.name, getattr(profile, field.name))
        if unclear and field.name not in confirming:
            if current is None or not same_value(current, value):
                contradictions[field.name] = Contradiction(
                    field=field.name,
                    current=current,
                    new=value,
                    reason="unclear_audio",
                    heard=_heard(state),
                )
            continue
        if current is None or field.name in confirming or same_value(current, value):
            updates[field.name] = value
            sources[field.name] = FieldSource(
                source="self_declared", captured_at=runtime.context.clock(), confidence=confidence
            )
            contradictions.pop(field.name, None)
            if field.name in declined:
                declined.remove(field.name)
        else:
            contradictions[field.name] = Contradiction(field=field.name, current=current, new=value)

    for name in confirming - answered:
        # Asked once, not answered: keep the known value (or, for unclear audio, nothing).
        contradictions.pop(name, None)
    for name in understanding.declined_fields:
        if name not in declined and name not in answered and getattr(profile, name) is None:
            declined.append(name)

    if dropped:
        log.info("profile_facts_dropped", count=dropped)
    merged = UserProfile.model_validate(
        {**profile.model_dump(), **updates, "field_sources": sources}
    )
    return {
        "profile": merged,
        "contradictions": list(contradictions.values()),
        "declined_fields": declined,
    }
