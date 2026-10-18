"""`check_eligibility`: the deterministic rule engine on every candidate (TRD §6, §10, FR-6).

Pure Python, no LLM. A state scheme is evaluated with an extra `state == <its state>` condition,
so it is never ELIGIBLE for someone from another state (or before the state is known), even if
its rule forgot to say so. Document requirements and application windows are added in M10.
"""

import structlog
from langgraph.runtime import Runtime

from sahayak.agent.catalogue import SchemeInfo
from sahayak.agent.deps import AgentDeps
from sahayak.agent.state import AgentState
from sahayak.db.models import SchemeLevel
from sahayak.eligibility.engine import EligibilityResult, evaluate
from sahayak.eligibility.rules import AllOf, Condition, SchemeRule

log = structlog.get_logger(__name__)


def effective_rule(scheme: SchemeInfo) -> SchemeRule | None:
    """The scheme's rule, restricted to its state for state schemes."""
    rule = scheme.rule
    if rule is None or scheme.level is not SchemeLevel.STATE or scheme.state is None:
        return rule
    in_state = Condition(field="state", op="==", value=scheme.state)
    return rule.model_copy(update={"logic": AllOf.model_validate({"all": [in_state, rule.logic]})})


async def check_eligibility(state: AgentState, runtime: Runtime[AgentDeps]) -> AgentState:
    catalogue = await runtime.context.catalogue.load()
    profile = state["profile"]
    results: dict[str, EligibilityResult] = {}
    for scheme_id in state.get("candidate_schemes", []):
        scheme = catalogue.get(scheme_id)
        rule = effective_rule(scheme) if scheme else None
        if rule is None:
            continue
        try:
            results[scheme_id] = evaluate(rule, profile)
        except (ValueError, AttributeError) as exc:
            # Only unreviewed drafts can be malformed: approved rules are validated on save.
            log.warning("rule_evaluation_failed", scheme_id=scheme_id, error=type(exc).__name__)
    return {"eligibility": results}
