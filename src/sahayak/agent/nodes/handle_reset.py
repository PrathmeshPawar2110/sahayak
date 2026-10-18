"""`handle_reset`: forget the conversation (TRD §10, FR-13).

"Start over" clears the profile, everything the agent tracked and the stored messages, keeping
only the language. "Delete my data" does the same here; the API then deletes the whole session,
its messages and its checkpoints (`delete_account`).
"""

from langchain_core.messages import AIMessage, RemoveMessage
from langgraph.graph.message import REMOVE_ALL_MESSAGES
from langgraph.runtime import Runtime

from sahayak.agent.deps import AgentDeps
from sahayak.agent.replies import message, with_pii_notice
from sahayak.agent.state import AgentReply, AgentState, DisplayResults
from sahayak.eligibility.profile import UserProfile


async def handle_reset(state: AgentState, runtime: Runtime[AgentDeps]) -> AgentState:
    language = state.get("language", "en")
    deleting = state.get("intent") == "delete_account"
    text = with_pii_notice(
        message("deleted" if deleting else "reset", language), state.get("pii_found", []), language
    )
    return {
        "profile": UserProfile(language=language),
        # Drop every stored message, including this request, then add the confirmation.
        "messages": [RemoveMessage(id=REMOVE_ALL_MESSAGES), AIMessage(text)],
        "candidate_schemes": [],
        "eligibility": {},
        "display_results": DisplayResults(),
        "retrieved_context": [],
        "pending_question": None,
        "selected_scheme": None,
        "questions_asked": 0,
        "declined_fields": [],
        "contradictions": [],
        "need": None,
        "focus_schemes": [],
        "named_schemes": [],
        "understanding": None,
        "reply": AgentReply(text=text, kind="reset"),
    }
