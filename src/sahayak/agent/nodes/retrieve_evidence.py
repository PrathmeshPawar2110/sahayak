"""`retrieve_evidence`: scheme passages a reply can cite (TRD §10, FR-7).

Passages are fetched per scheme, so every scheme in the reply gets its own evidence and no
scheme crowds out another: the displayed schemes on a discover turn, the named or chosen schemes
on a question about schemes. The search text is the English phrase the turn-understanding call
produced, or the scheme's name with "eligibility and benefits" when there is none. Named schemes
are searched whatever the user's state, so their true verdict can be explained.
"""

from langgraph.runtime import Runtime

from sahayak.agent.deps import AgentDeps
from sahayak.agent.state import AgentState, EvidenceChunk


def evidence_targets(state: AgentState, limit: int) -> list[str]:
    intent = state.get("intent", "discover")
    named = [*state.get("named_schemes", []), *state.get("focus_schemes", [])]
    selected = state.get("selected_scheme")
    if intent in ("select_scheme", "get_form"):
        targets = [selected, *named] if selected else named
    elif intent == "ask_scheme_info":
        targets = named
    else:
        targets = [*state["display_results"].scheme_ids(), *named]
    return list(dict.fromkeys(t for t in targets if t))[:limit]


async def retrieve_evidence(state: AgentState, runtime: Runtime[AgentDeps]) -> AgentState:
    deps = runtime.context
    catalogue = await deps.catalogue.load()
    understanding = state.get("understanding")
    query = understanding.search_query.strip() if understanding else ""
    chunks: dict[str, EvidenceChunk] = {}
    for scheme_id in evidence_targets(state, deps.config.evidence_schemes):
        scheme = catalogue.get(scheme_id)
        if scheme is None:
            continue
        found = await deps.passages.search(
            query or f"{scheme.name_en} eligibility and benefits",
            state=None,
            scheme_ids=[scheme_id],
            top_k=deps.config.evidence_per_scheme,
        )
        for chunk in found:
            chunks.setdefault(chunk.chunk_id, chunk)
    return {"retrieved_context": list(chunks.values())}
