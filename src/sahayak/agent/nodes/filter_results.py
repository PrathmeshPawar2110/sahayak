"""`filter_results`: the only schemes a reply may recommend (TRD §8C, FR-26, FR-27).

Deterministic. Basic version (M4): only ELIGIBLE schemes are shown. `active` schemes go to
`ready` (no document requirements are checked before M10, so a rule without them has nothing
left to check); `provisional` ones to the separately labelled `provisional` group, only while
`SHOW_PROVISIONAL_SCHEMES` is on. NOT_ELIGIBLE and NEEDS_INFO schemes, and discontinued or
archived ones, are never shown. M10 adds documents, application windows and sorting by benefit
and deadline.
"""

from langgraph.runtime import Runtime

from sahayak.agent.catalogue import RECOMMENDABLE
from sahayak.agent.deps import AgentDeps
from sahayak.agent.state import AgentState, DisplayItem, DisplayResults
from sahayak.eligibility.engine import Verdict


async def filter_results(state: AgentState, runtime: Runtime[AgentDeps]) -> AgentState:
    deps = runtime.context
    catalogue = await deps.catalogue.load()
    ready: list[DisplayItem] = []
    provisional: list[DisplayItem] = []
    for scheme_id, result in sorted(state.get("eligibility", {}).items()):
        scheme = catalogue.get(scheme_id)
        if (
            scheme is None
            or result.verdict is not Verdict.ELIGIBLE
            or scheme.status not in RECOMMENDABLE
        ):
            continue
        item = DisplayItem(
            scheme_id=scheme_id,
            rule_version=result.rule_version,
            provisional=scheme.provisional,
            reasons=[check.text() for check in result.passed],
        )
        if not scheme.provisional:
            ready.append(item)
        elif deps.config.show_provisional:
            provisional.append(item)
    return {"display_results": DisplayResults(ready=ready, provisional=provisional)}
