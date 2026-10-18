from typing import Any

from hypothesis import given
from hypothesis import strategies as st

from sahayak.eligibility.engine import EligibilityResult, Verdict, evaluate_all
from sahayak.eligibility.planner import DEFAULT_MAX_QUESTIONS, next_question
from sahayak.eligibility.profile import UserProfile
from sahayak.eligibility.rules import SchemeRule, validate_rule


def rule(scheme_id: str, *conditions: dict[str, Any]) -> SchemeRule:
    return validate_rule(
        {
            "scheme_id": scheme_id,
            "version": 1,
            "status": "approved",
            "logic": {"all": list(conditions)},
        }
    )


AGE_18 = {"field": "age", "op": ">=", "value": 18}
NO_TAX = {"field": "is_income_tax_payer", "op": "==", "value": False}
FEMALE = {"field": "gender", "op": "==", "value": "female"}
MH = {"field": "state", "op": "==", "value": "MH"}
OBC = {"field": "social_category", "op": "==", "value": "obc"}
RULES = [
    rule("apy", AGE_18, NO_TAX),
    rule("pmuy", AGE_18, FEMALE),
    rule("ladki-bahin", AGE_18, FEMALE, MH),
    rule("mjpjay", MH),
]


def plan(profile: UserProfile, rules: list[SchemeRule] = RULES, **kwargs: Any) -> str | None:
    question = next_question(
        evaluate_all(rules, profile), profile, **{"questions_asked": 0, **kwargs}
    )
    return question.field if question else None


def test_asks_the_field_most_needs_info_schemes_need() -> None:
    question = next_question(
        evaluate_all(RULES, UserProfile(language="hi")),
        UserProfile(language="hi"),
        questions_asked=0,
    )

    assert question is not None
    assert question.field == "age"
    assert question.scheme_ids == ("apy", "ladki-bahin", "pmuy")
    assert not question.sensitive


def test_follows_answers_through_a_conversation() -> None:
    profile = UserProfile(language="mr")
    asked: list[str] = []
    answers = {"age": 30, "gender": "female", "state": "MH", "is_income_tax_payer": False}
    while (field := plan(profile, questions_asked=len(asked))) is not None:
        asked.append(field)
        setattr(profile, field, answers[field])

    # gender and state each unblock two schemes; ties go to profile field order.
    assert asked == ["age", "state", "gender", "is_income_tax_payer"]
    assert {r.verdict for r in evaluate_all(RULES, profile).values()} == {Verdict.ELIGIBLE}


def test_ruled_out_schemes_stop_driving_questions() -> None:
    profile = UserProfile(language="en", age=12)

    # Every rule except mjpjay needs age >= 18, so only state is worth asking.
    assert plan(profile) == "state"


def test_never_asks_for_known_or_declined_fields() -> None:
    profile = UserProfile(language="en", age=30)
    stale = {
        "apy": EligibilityResult(
            scheme_id="apy",
            rule_version=1,
            verdict=Verdict.NEEDS_INFO,
            passed=[],
            failed=[],
            missing_fields=["age", "is_income_tax_payer"],
        )
    }

    question = next_question(stale, profile, questions_asked=0)
    assert question is not None
    assert question.field == "is_income_tax_payer"
    assert (
        next_question(stale, profile, questions_asked=0, declined={"is_income_tax_payer"}) is None
    )


def test_stops_at_the_question_limit() -> None:
    profile = UserProfile(language="en")

    assert plan(profile, questions_asked=DEFAULT_MAX_QUESTIONS - 1) == "age"
    assert plan(profile, questions_asked=DEFAULT_MAX_QUESTIONS) is None
    assert plan(profile, questions_asked=2, max_questions=2) is None


def test_nothing_to_ask_when_everything_is_decided() -> None:
    profile = UserProfile(
        language="en", age=30, gender="male", state="UP", is_income_tax_payer=True
    )

    assert plan(profile) is None


def test_sensitive_fields_lose_ties() -> None:
    rules = [rule("obc-scholarship", OBC, {"field": "marks_percent", "op": ">=", "value": 60})]

    question = next_question(
        evaluate_all(rules, UserProfile(language="en")),
        UserProfile(language="en"),
        questions_asked=0,
    )

    assert question is not None
    assert question.field == "marks_percent"
    assert plan(UserProfile(language="en", marks_percent=75.0), rules) == "social_category"


def test_sensitive_field_is_asked_when_it_helps_most() -> None:
    rules = [rule("a", OBC), rule("b", OBC, AGE_18), rule("c", AGE_18, MH), rule("d", OBC)]

    question = next_question(
        evaluate_all(rules, UserProfile(language="en")),
        UserProfile(language="en"),
        questions_asked=0,
    )

    assert question is not None
    assert (question.field, question.sensitive) == ("social_category", True)


@given(
    st.fixed_dictionaries(
        {
            "age": st.none() | st.integers(0, 100),
            "gender": st.none() | st.sampled_from(["male", "female"]),
            "state": st.none() | st.sampled_from(["MH", "UP"]),
            "is_income_tax_payer": st.none() | st.booleans(),
            "social_category": st.none() | st.sampled_from(["obc", "sc"]),
        }
    ),
    st.integers(0, 10),
)
def test_planned_field_is_unknown_and_needed(values: dict[str, Any], asked: int) -> None:
    profile = UserProfile.model_validate({"language": "en", **values})
    results = evaluate_all([*RULES, rule("obc", OBC)], profile)

    question = next_question(results, profile, questions_asked=asked)

    if question is None:
        assert asked >= DEFAULT_MAX_QUESTIONS or all(
            r.verdict is not Verdict.NEEDS_INFO for r in results.values()
        )
        return
    assert getattr(profile, question.field) is None
    counts = {f: sum(f in r.missing_fields for r in results.values()) for f in values}
    assert counts[question.field] == max(counts.values()) == len(question.scheme_ids)
