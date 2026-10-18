"""`compose_response`: the reply, grounded in the rule engine and in cited passages (TRD §10).

Turns that need no scheme information (greetings, out-of-scope questions, features that arrive
later) get a fixed reply. Otherwise the LLM writes the reply as structured data (prompt
`response_composition`), which `sahayak.agent.guardrails` checks: only allowed schemes, true
verdicts, every point cited. A rejected draft is regenerated once with the reasons; if that fails
too, or the LLM is down, a fixed reply that only lists verdicts is sent (D-036).
"""

import structlog
from langchain_core.messages import AIMessage
from langgraph.runtime import Runtime

from sahayak.agent.catalogue import Catalogue
from sahayak.agent.conversation import latest_user_text
from sahayak.agent.deps import AgentDeps
from sahayak.agent.guardrails import (
    ComposedReply,
    SchemeFacts,
    check_reply,
    render_reply,
    scheme_facts,
    template_reply,
)
from sahayak.agent.replies import LANGUAGE_NAMES, message
from sahayak.agent.state import AgentReply, AgentState, Citation, EvidenceChunk, GuardOutcome
from sahayak.agent.untrusted import as_attribute, as_data
from sahayak.eligibility.profile import Language
from sahayak.llm.base import ChatMessage, LLMError
from sahayak.llm.prompts import load_prompt

log = structlog.get_logger(__name__)

PROMPT_NAME = "response_composition"

# Intents answered with a fixed message, no LLM and no scheme information.
FIXED_REPLIES = {
    "smalltalk": "smalltalk",
    "out_of_scope": "out_of_scope",
    "upload_document": "not_available",  # M10
    "my_documents": "not_available",  # M10
    "manage_reminders": "not_available",  # M11
}

TASKS = {
    "discover": (
        "Tell the citizen which schemes they may be eligible for (those marked 'show this to "
        "the citizen'), each with a short reason and its main benefit."
    ),
    "discover_none": (
        "Tell the citizen honestly that, with the details shared so far, no scheme fits yet. "
        "Don't suggest any scheme."
    ),
    "ask_scheme_info": "Answer the citizen's question about the scheme they named.",
    "select_scheme": (
        "The citizen chose this scheme. Explain its main benefit, the documents needed and how "
        "to apply, from the passages."
    ),
}


def _task(intent: str, facts: dict[str, SchemeFacts]) -> str:
    if intent == "discover":
        return TASKS["discover" if any(f.displayed for f in facts.values()) else "discover_none"]
    if intent in ("select_scheme", "get_form"):
        return TASKS["select_scheme"]
    return TASKS["ask_scheme_info"]


async def _draft(
    deps: AgentDeps,
    *,
    language: Language,
    facts: dict[str, SchemeFacts],
    evidence: list[EvidenceChunk],
    task: str,
    user_message: str,
    problems: list[str],
) -> ComposedReply:
    prompt = load_prompt(PROMPT_NAME).render(
        language_name=LANGUAGE_NAMES[language],
        schemes=list(facts.values()),
        task=task,
        evidence=[
            {
                "chunk_id": as_attribute(chunk.chunk_id),
                "scheme_id": as_attribute(chunk.scheme_id),
                "section": as_attribute(chunk.section),
                "text": as_data(chunk.text),
            }
            for chunk in evidence
        ],
        message=as_data(user_message),
        retry_problems=problems,
    )
    response = await deps.llm.structured(
        [ChatMessage("user", prompt.user)], ComposedReply, system=prompt.system, max_tokens=1500
    )
    return response.value


async def compose(
    deps: AgentDeps, state: AgentState, catalogue: Catalogue
) -> tuple[str, list[Citation], GuardOutcome]:
    """The reply text, its citations, and how it passed the post-check."""
    language = state.get("language", "en")
    intent = state.get("intent", "discover")
    named = [*state.get("named_schemes", []), *state.get("focus_schemes", [])]
    if selected := state.get("selected_scheme"):
        named.append(selected)
    facts = scheme_facts(
        catalogue,
        language,
        state.get("eligibility", {}),
        state["display_results"].scheme_ids() if intent == "discover" else [],
        named,
    )
    evidence = state.get("retrieved_context", [])
    by_id = {chunk.chunk_id: chunk for chunk in evidence}
    problems: list[str] = []
    for attempt in range(2):
        try:
            draft = await _draft(
                deps,
                language=language,
                facts=facts,
                evidence=evidence,
                task=_task(intent, facts),
                user_message=latest_user_text(state),
                problems=problems,
            )
        except LLMError as exc:
            log.warning("compose_failed", error=type(exc).__name__)
            break
        problems = check_reply(draft, allowed=facts, evidence=by_id, catalogue=catalogue)
        if not problems:
            text, citations = render_reply(
                draft, allowed=facts, evidence=by_id, catalogue=catalogue, language=language
            )
            return text, citations, "passed" if attempt == 0 else "regenerated"
        log.info("reply_rejected", attempt=attempt + 1, problems=len(problems))
    text = template_reply(facts, results=intent == "discover", language=language)
    return text, [], "template"


async def compose_response(state: AgentState, runtime: Runtime[AgentDeps]) -> AgentState:
    deps = runtime.context
    language = state.get("language", "en")
    intent = state.get("intent", "discover")
    citations: list[Citation] = []
    guard: GuardOutcome | None = None
    if intent in FIXED_REPLIES:
        text = message(FIXED_REPLIES[intent], language)
    else:
        catalogue = await deps.catalogue.load()
        text, citations, guard = await compose(deps, state, catalogue)
        if intent == "get_form":
            text = f"{text}\n\n{message('form_not_available', language)}"
    reply = AgentReply(text=text, kind="answer", citations=citations, guard=guard)
    return {"reply": reply, "pending_question": None, "messages": [AIMessage(text)]}
