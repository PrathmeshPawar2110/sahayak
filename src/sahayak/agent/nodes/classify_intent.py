"""`classify_intent`: what the user wants, and in which language (TRD §10, FR-2).

One structured LLM call (prompt `turn_understanding`) reads the latest message: its intent, its
language, the profile facts it states, the schemes it names and English search text for it.
`extract_profile` merges the facts deterministically, so a turn needs one LLM call to
understand the user instead of two (see D-033).
"""

import re

from langgraph.runtime import Runtime

from sahayak.agent.conversation import history, latest_user_text
from sahayak.agent.deps import AgentDeps
from sahayak.agent.state import AgentState, FieldValue, TurnUnderstanding
from sahayak.agent.untrusted import as_data
from sahayak.eligibility.fields import PROFILE_FIELDS
from sahayak.llm.base import ChatMessage
from sahayak.llm.prompts import load_prompt

PROMPT_NAME = "turn_understanding"
SENSITIVE_FIELDS = tuple(name for name, field in PROFILE_FIELDS.items() if field.sensitive)

# A message without letters ("45", "ok!") says nothing about the language being spoken.
_LETTERS = re.compile(r"[^\W\d_]")


def _fact_text(value: FieldValue) -> str:
    """A value written the way the prompt asks the LLM to write facts."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


async def classify_intent(state: AgentState, runtime: Runtime[AgentDeps]) -> AgentState:
    deps = runtime.context
    message = latest_user_text(state)
    language = state.get("language", "en")
    prompt = load_prompt(PROMPT_NAME).render(
        language=language,
        pending=state.get("pending_question"),
        heard=[
            {"field": c.field, "value": _fact_text(c.new)}
            for c in state.get("contradictions", [])
            if c.reason == "unclear_audio" and c.asked
        ],
        history=history(state, deps.config.history_messages),
        message=as_data(message),
        fields=PROFILE_FIELDS.values(),
        sensitive_allowed="sensitive" in state.get("consents", []),
        sensitive_fields=SENSITIVE_FIELDS,
    )
    response = await deps.llm.structured(
        [ChatMessage("user", prompt.user)],
        TurnUnderstanding,
        system=prompt.system,
        max_tokens=1000,
    )
    understanding = response.value
    detected = understanding.language if _LETTERS.search(message) else language
    return {
        "understanding": understanding,
        "intent": understanding.intent,
        "language": detected,
        "profile": state["profile"].model_copy(update={"language": detected}),
    }
