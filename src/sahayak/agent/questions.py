"""Whether the agent should ask a question this turn, and which one (TRD §10, FR-5, FR-14).

Shared by the graph's router and the `plan_question` node, so both always agree. Questions are
asked only on `discover` turns. A contradiction to confirm comes first; otherwise the planner
(M2) picks the missing field most NEEDS_INFO schemes need. When the user named schemes, only
those drive questions, so "am I eligible for X?" asks only what X needs. Every question,
confirmations included, counts towards MAX_QUESTIONS_PER_SESSION.
"""

from sahayak.agent.deps import AgentConfig
from sahayak.agent.state import AgentState, Contradiction
from sahayak.eligibility.fields import PROFILE_FIELDS
from sahayak.eligibility.planner import PlannedQuestion, next_question

SENSITIVE_FIELDS = frozenset(name for name, field in PROFILE_FIELDS.items() if field.sensitive)


def off_limits(state: AgentState) -> set[str]:
    """Fields not to ask: declined ones, and sensitive ones without the user's consent."""
    blocked = set(state.get("declined_fields", []))
    if "sensitive" not in state.get("consents", []):
        blocked |= SENSITIVE_FIELDS
    return blocked


def contradiction_to_confirm(state: AgentState) -> Contradiction | None:
    return next((c for c in state.get("contradictions", []) if not c.asked), None)


def choose_question(state: AgentState, config: AgentConfig) -> PlannedQuestion | None:
    results = state.get("eligibility", {})
    focus = [scheme_id for scheme_id in state.get("focus_schemes", []) if scheme_id in results]
    if focus:
        results = {scheme_id: results[scheme_id] for scheme_id in focus}
    return next_question(
        results,
        state["profile"],
        questions_asked=state.get("questions_asked", 0),
        max_questions=config.max_questions,
        declined=off_limits(state),
    )


def should_ask(state: AgentState, config: AgentConfig) -> bool:
    if state.get("intent") != "discover":
        return False
    if contradiction_to_confirm(state) is not None:
        return state.get("questions_asked", 0) < config.max_questions
    return choose_question(state, config) is not None
