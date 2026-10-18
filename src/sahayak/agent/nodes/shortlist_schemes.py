"""`shortlist_schemes`: which schemes to evaluate this turn (TRD §10).

Candidates are the schemes with a rule that may apply in the user's state (central schemes plus
that state's; every state's while the state is unknown), `active` or `provisional` only. When the
user has stated a need ("pension", "housing"), only schemes whose passages match it are kept.
Schemes the user names are always evaluated, even outside their state, so the agent can give
their true verdict.

The need and the named schemes ("focus") carry over to the user's answers to follow-up
questions. A new request without a topic or a name ("which schemes can I get?") clears them.
"""

from langgraph.runtime import Runtime

from sahayak.agent.catalogue import RECOMMENDABLE, Catalogue
from sahayak.agent.conversation import latest_user_text
from sahayak.agent.deps import AgentDeps
from sahayak.agent.state import AgentState, TurnUnderstanding


def named_schemes(catalogue: Catalogue, message: str, names: list[str]) -> list[str]:
    """Catalogue ids of the schemes the user named, in the message or as the LLM read them."""
    found = catalogue.mentions(message)
    for name in names:
        found += catalogue.mentions(name)
    return list(dict.fromkeys(found))


def _is_new_request(understanding: TurnUnderstanding) -> bool:
    """A discover turn that only asks in general: no facts, answers, names or topic."""
    return understanding.intent == "discover" and not (
        understanding.facts
        or understanding.declined_fields
        or understanding.scheme_names
        or understanding.search_query.strip()
    )


async def shortlist_schemes(state: AgentState, runtime: Runtime[AgentDeps]) -> AgentState:
    deps = runtime.context
    catalogue = await deps.catalogue.load()
    understanding = state.get("understanding")
    profile = state["profile"]
    named = named_schemes(
        catalogue,
        latest_user_text(state),
        understanding.scheme_names if understanding else [],
    )

    need = state.get("need")
    focus = list(state.get("focus_schemes", []))
    selected = state.get("selected_scheme")
    if understanding is not None:
        query = understanding.search_query.strip()
        if named:
            focus = named
        elif _is_new_request(understanding):
            focus, need = [], None
        if understanding.intent == "discover" and query:
            need = query
        if understanding.intent in ("select_scheme", "get_form") and named:
            selected = named[0]

    evaluable = {scheme.id for scheme in catalogue.evaluable(profile.state)}
    candidates = set(evaluable)
    if need:
        hits = await deps.passages.search(
            need, state=profile.state, top_k=deps.config.shortlist_passages
        )
        matching = {hit.scheme_id for hit in hits} & evaluable
        candidates = matching or evaluable  # a need that matches nothing narrows nothing
    candidates |= {
        scheme_id
        for scheme_id in [*focus, *named]
        if (scheme := catalogue.get(scheme_id)) is not None
        and scheme.rule is not None
        and scheme.status in RECOMMENDABLE
    }
    return {
        "candidate_schemes": sorted(candidates),
        "named_schemes": named,
        "focus_schemes": focus,
        "need": need,
        "selected_scheme": selected,
    }
