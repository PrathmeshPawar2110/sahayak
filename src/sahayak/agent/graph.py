"""The agent as a LangGraph state machine (TRD §10).

    START -> load_user_context -> classify_intent
    classify_intent --discover / scheme questions--> extract_profile -> shortlist_schemes
        -> check_eligibility -> filter_results
    filter_results --a question is needed--> plan_question -> END
    filter_results --otherwise--> retrieve_evidence -> compose_response -> END
    classify_intent --reset / delete_account--> handle_reset -> END
    classify_intent --anything else--> compose_response -> END

Each session is one checkpointer thread (`thread_id` = session id).
"""

from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.runtime import Runtime

from sahayak.agent.deps import AgentDeps
from sahayak.agent.nodes.check_eligibility import check_eligibility
from sahayak.agent.nodes.classify_intent import classify_intent
from sahayak.agent.nodes.compose_response import compose_response
from sahayak.agent.nodes.extract_profile import extract_profile
from sahayak.agent.nodes.filter_results import filter_results
from sahayak.agent.nodes.load_user_context import load_user_context
from sahayak.agent.nodes.plan_question import plan_question
from sahayak.agent.nodes.retrieve_evidence import retrieve_evidence
from sahayak.agent.nodes.shortlist_schemes import shortlist_schemes
from sahayak.agent.questions import should_ask
from sahayak.agent.state import AgentState

type AgentGraph = CompiledStateGraph[AgentState, AgentDeps, AgentState, AgentState]


async def _pending(state: AgentState, runtime: Runtime[AgentDeps]) -> AgentState:
    """Placeholder for a node that later tasks implement."""
    return {}


def route_intent(state: AgentState) -> str:
    intent = state.get("intent", "discover")
    if intent in ("reset", "delete_account"):
        return "handle_reset"
    if intent in ("discover", "ask_scheme_info", "select_scheme", "get_form"):
        return "extract_profile"
    return "compose_response"


def route_results(state: AgentState, runtime: Runtime[AgentDeps]) -> str:
    return "plan_question" if should_ask(state, runtime.context.config) else "retrieve_evidence"


def build_graph(checkpointer: BaseCheckpointSaver[Any] | None = None) -> AgentGraph:
    graph = StateGraph(
        AgentState, context_schema=AgentDeps, input_schema=AgentState, output_schema=AgentState
    )
    graph.add_node("load_user_context", load_user_context)
    graph.add_node("classify_intent", classify_intent)
    graph.add_node("extract_profile", extract_profile)
    graph.add_node("shortlist_schemes", shortlist_schemes)
    graph.add_node("check_eligibility", check_eligibility)
    graph.add_node("filter_results", filter_results)
    graph.add_node("plan_question", plan_question)
    graph.add_node("retrieve_evidence", retrieve_evidence)
    graph.add_node("compose_response", compose_response)
    graph.add_node("handle_reset", _pending)

    graph.add_edge(START, "load_user_context")
    graph.add_edge("load_user_context", "classify_intent")
    graph.add_conditional_edges(
        "classify_intent", route_intent, ["handle_reset", "extract_profile", "compose_response"]
    )
    graph.add_edge("extract_profile", "shortlist_schemes")
    graph.add_edge("shortlist_schemes", "check_eligibility")
    graph.add_edge("check_eligibility", "filter_results")
    graph.add_conditional_edges(
        "filter_results", route_results, ["plan_question", "retrieve_evidence"]
    )
    graph.add_edge("plan_question", END)
    graph.add_edge("retrieve_evidence", "compose_response")
    graph.add_edge("compose_response", END)
    graph.add_edge("handle_reset", END)
    return graph.compile(checkpointer=checkpointer)
