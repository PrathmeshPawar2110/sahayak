"""`plan_question`: ask for the one detail that settles the most schemes (TRD §10, FR-5, FR-14).

A contradiction, or what was heard in a voice message the speech-to-text was unsure about, is
confirmed first, with fixed wording. Otherwise the planner picks the field
(`sahayak.agent.questions`) and the LLM words the question in the user's language (prompt
`question_wording`). The fixed wording in `replies.yaml` is used instead when the LLM fails, or
when its question is empty, too long, or names a scheme: a question must never hint at a
scheme the user may not qualify for.
"""

import structlog
from langchain_core.messages import AIMessage
from langgraph.runtime import Runtime
from pydantic import BaseModel, ConfigDict

from sahayak.agent.catalogue import Catalogue
from sahayak.agent.conversation import latest_user_text
from sahayak.agent.deps import AgentDeps
from sahayak.agent.questions import choose_question, contradiction_to_confirm
from sahayak.agent.replies import (
    LANGUAGE_NAMES,
    format_value,
    message,
    question,
    with_pii_notice,
)
from sahayak.agent.state import AgentReply, AgentState, PendingQuestion
from sahayak.agent.untrusted import as_data
from sahayak.eligibility.fields import PROFILE_FIELDS
from sahayak.eligibility.planner import PlannedQuestion
from sahayak.eligibility.profile import Language
from sahayak.llm.base import ChatMessage, LLMError
from sahayak.llm.prompts import load_prompt

log = structlog.get_logger(__name__)

PROMPT_NAME = "question_wording"
MAX_QUESTION_CHARS = 400


class QuestionWording(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str


async def word_question(
    deps: AgentDeps,
    catalogue: Catalogue,
    planned: PlannedQuestion,
    language: Language,
    last_message: str,
) -> str:
    """The question in the user's language: LLM wording if it passes the checks."""
    fallback = question(planned.field, language)
    prompt = load_prompt(PROMPT_NAME).render(
        language_name=LANGUAGE_NAMES[language],
        field=PROFILE_FIELDS[planned.field],
        sensitive=planned.sensitive,
        example=question(planned.field, "en"),
        message=as_data(last_message),
    )
    try:
        response = await deps.llm.structured(
            [ChatMessage("user", prompt.user)],
            QuestionWording,
            system=prompt.system,
            max_tokens=300,
        )
    except LLMError as exc:
        log.warning("question_wording_failed", field=planned.field, error=type(exc).__name__)
        return fallback
    text = " ".join(response.value.question.split())
    if not text or len(text) > MAX_QUESTION_CHARS or catalogue.mentions(text):
        log.info("question_wording_rejected", field=planned.field)
        return fallback
    return text


async def plan_question(state: AgentState, runtime: Runtime[AgentDeps]) -> AgentState:
    deps = runtime.context
    language = state.get("language", "en")
    update: AgentState = {}
    contradiction = contradiction_to_confirm(state)
    if contradiction is not None and contradiction.reason == "unclear_audio":
        # One read-back confirms every value heard in that message.
        text = message("confirm_heard", language, heard=contradiction.heard or "")
        pending = PendingQuestion(field=contradiction.field, kind="confirm_heard")
        update["contradictions"] = [
            c.model_copy(update={"asked": True}) if c.reason == "unclear_audio" else c
            for c in state.get("contradictions", [])
        ]
    elif contradiction is not None:
        current = contradiction.current
        text = message(
            "confirm",
            language,
            old="" if current is None else format_value(current, language),
            new=format_value(contradiction.new, language),
        )
        pending = PendingQuestion(field=contradiction.field, kind="confirm")
        update["contradictions"] = [
            c.model_copy(update={"asked": True}) if c is contradiction else c
            for c in state.get("contradictions", [])
        ]
    else:
        planned = choose_question(state, deps.config)
        if planned is None:
            raise AssertionError("plan_question runs only when there is a question to ask")
        catalogue = await deps.catalogue.load()
        text = await word_question(deps, catalogue, planned, language, latest_user_text(state))
        pending = PendingQuestion(field=planned.field, kind="ask")
    text = with_pii_notice(text, state.get("pii_found", []), language)
    reply = AgentReply(text=text, kind="question", question_field=pending.field)
    return {
        **update,
        "reply": reply,
        "pending_question": pending,
        "questions_asked": state.get("questions_asked", 0) + 1,
        "messages": [AIMessage(text)],
    }
