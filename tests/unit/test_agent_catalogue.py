import pytest

from sahayak.agent.catalogue import Catalogue, choose_rule
from sahayak.db.models import SchemeStatus
from tests.agent_support import CATALOGUE, FARMER, m1_catalogue, rule, scheme

APPROVED = rule("x", {"all": [{"field": "age", "op": ">=", "value": 18}]})
DRAFT = rule("x", {"all": [{"field": "age", "op": ">=", "value": 21}]}, status="draft")


@pytest.mark.parametrize(
    ("status", "show", "approved", "draft", "expected"),
    [
        (SchemeStatus.ACTIVE, True, APPROVED, DRAFT, APPROVED),
        (SchemeStatus.ACTIVE, True, None, DRAFT, None),  # drafts never decide active schemes
        (SchemeStatus.PROVISIONAL, True, None, DRAFT, DRAFT),
        (SchemeStatus.PROVISIONAL, True, APPROVED, DRAFT, APPROVED),
        (SchemeStatus.PROVISIONAL, False, APPROVED, DRAFT, None),
        (SchemeStatus.DISCONTINUED, True, APPROVED, DRAFT, None),
        (SchemeStatus.ARCHIVED, True, APPROVED, DRAFT, None),
    ],
)
def test_choose_rule(
    status: SchemeStatus, show: bool, approved: object, draft: object, expected: object
) -> None:
    assert choose_rule(status, approved, draft, show_provisional=show) is expected  # type: ignore[arg-type]


def test_names_include_the_abbreviation_in_brackets() -> None:
    names = FARMER.names()
    assert {"Farm Aid Scheme (FAS)", "Farm Aid Scheme", "FAS", "Farm Aid", "farm-aid"} <= set(names)


def test_evaluable_schemes_by_state() -> None:
    def ids(state: str | None) -> list[str]:
        return sorted(s.id for s in CATALOGUE.evaluable(state))

    # The discontinued scheme is never evaluable; the MH scheme only in MH or before the state
    # is known.
    assert ids(None) == ["farm-aid", "mh-girls", "new-housing", "old-pension"]
    assert ids("MH") == ["farm-aid", "mh-girls", "new-housing", "old-pension"]
    assert ids("UP") == ["farm-aid", "new-housing", "old-pension"]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Tell me about the Farm Aid Scheme", ["farm-aid"]),
        ("is FAS or OAP better?", ["farm-aid", "old-pension"]),
        ("farm-aid", ["farm-aid"]),
        ("I need fast help", []),  # FAS only as a whole word
        ("किसान सहायता योजना के बारे में बताइए", ["farm-aid"]),
        ("", []),
    ],
)
def test_mentions(text: str, expected: list[str]) -> None:
    assert CATALOGUE.mentions(text) == expected


def test_overlapping_names_go_to_the_longest_match() -> None:
    catalogue = Catalogue.of(
        [
            scheme("pmjay", "Pradhan Mantri Jan Arogya Yojana", None),
            scheme("mjpjay", "Phule Health Scheme", None, aliases=["Jan Arogya Yojana"]),
        ]
    )
    assert catalogue.mentions("Pradhan Mantri Jan Arogya Yojana covers Rs 5 lakh") == ["pmjay"]
    assert catalogue.mentions("What is Jan Arogya Yojana?") == ["mjpjay"]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Am I eligible for PM Kisan?", ["pm-kisan"]),
        ("pmkisan and PM-SYM", ["pm-kisan", "pm-sym"]),
        ("प्रधानमंत्री किसान सम्मान निधि क्या है?", ["pm-kisan"]),
        ("मला पीएम किसानसाठी अर्ज करायचा आहे", ["pm-kisan"]),
        ("Ayushman Bharat Pradhan Mantri Jan Arogya Yojana", ["ab-pmjay"]),
        ("Mahatma Jyotirao Phule Jan Arogya Yojana", ["mh-mjpjay"]),
        ("आयुष्मान भारत कार्ड", ["ab-pmjay"]),
        ("लाडकी बहीण योजनेसाठी", ["mh-majhi-ladki-bahin"]),
        ("Sukanya Samriddhi account for my daughter", ["ssy"]),
        ("I want a pension", []),
    ],
)
def test_mentions_in_the_m1_catalogue(text: str, expected: list[str]) -> None:
    assert m1_catalogue().mentions(text) == expected
