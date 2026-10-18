"""Which question to ask next (TRD §10 `plan_question`, FR-5).

Greedy: among the schemes still in NEEDS_INFO, pick the missing field that the most of them
need. Ties go to non-sensitive fields first (TRD §8B: caste, religion and disability are asked
only when nothing else would help as much), then to the field order of `UserProfile`. The planner
never picks a field the profile already knows or one the user declined to answer, and stops after
`max_questions`. Phrasing the question in the user's language is the agent's job (M4).
"""

from collections.abc import Collection, Mapping
from dataclasses import dataclass

from sahayak.eligibility.engine import EligibilityResult, Verdict
from sahayak.eligibility.fields import PROFILE_FIELDS
from sahayak.eligibility.profile import UserProfile

DEFAULT_MAX_QUESTIONS = 8  # TRD §15 MAX_QUESTIONS_PER_SESSION

_ORDER = {name: index for index, name in enumerate(PROFILE_FIELDS)}


@dataclass(frozen=True)
class PlannedQuestion:
    field: str
    scheme_ids: tuple[str, ...]  # the NEEDS_INFO schemes this answer feeds into
    sensitive: bool


def next_question(
    results: Mapping[str, EligibilityResult],
    profile: UserProfile,
    *,
    questions_asked: int,
    max_questions: int = DEFAULT_MAX_QUESTIONS,
    declined: Collection[str] = (),
) -> PlannedQuestion | None:
    """The best field to ask about next, or None when there is nothing (more) to ask."""
    if questions_asked >= max_questions:
        return None
    needing: dict[str, list[str]] = {}
    for scheme_id, result in results.items():
        if result.verdict is not Verdict.NEEDS_INFO:
            continue
        for field in result.missing_fields:
            if field not in declined and getattr(profile, field) is None:
                needing.setdefault(field, []).append(scheme_id)
    if not needing:
        return None
    field = min(
        needing,
        key=lambda name: (-len(needing[name]), PROFILE_FIELDS[name].sensitive, _ORDER[name]),
    )
    return PlannedQuestion(
        field=field,
        scheme_ids=tuple(sorted(needing[field])),
        sensitive=PROFILE_FIELDS[field].sensitive,
    )
