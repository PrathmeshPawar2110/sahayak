"""`load_user_context`: start a turn (TRD §10).

For an anonymous session (M4) this sets up a new conversation's state on its first turn and
clears the fields that only live for one turn. From M10 it also loads a signed-in user's saved
profile, field sources and document summaries.
"""

from langgraph.runtime import Runtime

from sahayak.agent.deps import AgentDeps
from sahayak.agent.state import AgentState, DisplayResults
from sahayak.eligibility.profile import UserProfile


async def load_user_context(state: AgentState, runtime: Runtime[AgentDeps]) -> AgentState:
    language = state.get("language", "en")
    profile = state.get("profile") or UserProfile(language=language)
    return {
        "language": language,
        "profile": profile,
        "turn_count": state.get("turn_count", 0) + 1,
        "questions_asked": state.get("questions_asked", 0),
        "declined_fields": state.get("declined_fields", []),
        "contradictions": state.get("contradictions", []),
        "pending_question": state.get("pending_question"),
        "candidate_schemes": state.get("candidate_schemes", []),
        "eligibility": state.get("eligibility", {}),
        "selected_scheme": state.get("selected_scheme"),
        "need": state.get("need"),
        "focus_schemes": state.get("focus_schemes", []),
        "consents": state.get("consents", []),
        "pii_found": state.get("pii_found", []),
        "asr_confidence": state.get("asr_confidence"),
        # Anonymous until M10 adds accounts and the document vault.
        "user_id": None,
        "member_id": None,
        "documents": [],
        # This turn only.
        "intent": "discover",
        "understanding": None,
        "named_schemes": [],
        "display_results": DisplayResults(),
        "retrieved_context": [],
        "reply": None,
    }
