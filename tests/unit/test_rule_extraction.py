import pytest
from pydantic import ValidationError

from sahayak.ingestion.manifest import SchemeSpec
from sahayak.ingestion.parse import Section
from sahayak.ingestion.rule_extraction import (
    PROMPT_NAME,
    Excerpt,
    RuleExtraction,
    SourceText,
    excerpts_hash,
    review_issues,
    select_excerpts,
    to_logic,
)
from sahayak.llm.prompts import load_prompt

URL = "https://example.gov.in/scheme.pdf"

EXTRACTION = {
    "combinator": "all",
    "items": [
        {"field": "occupation", "op": "in", "value": ["farmer"]},
        {
            "negate": True,
            "combinator": "any",
            "conditions": [
                {"field": "is_income_tax_payer", "op": "==", "value": True},
                {"field": "disability_percent", "op": "exists", "value": None},
            ],
        },
    ],
    "source_refs": [{"url": URL, "section": "Exclusions"}],
    "unmapped_criteria": ["Institutional landholders"],
    "notes": "",
    "confidence": 0.8,
}


def source(*sections: tuple[str, str]) -> SourceText:
    return SourceText(URL, [Section(heading, text) for heading, text in sections])


def test_everything_is_sent_when_it_fits() -> None:
    excerpts, truncated = select_excerpts(
        [source(("Overview", "About the scheme."), ("Empty", "  "), ("Benefits", "Rs 6000."))]
    )

    assert [e.section for e in excerpts] == ["Overview", "Benefits"]
    assert truncated is False


def test_only_eligibility_sections_are_sent_when_too_long() -> None:
    long_text = "x" * 2000
    sources = [
        source(
            ("Overview", long_text),
            ("Eligibility", "Small farmers."),
            ("Procedure", f"{long_text} Exclusion of tax payers applies."),
            ("पात्रता", "किसान परिवार"),
        )
    ]

    excerpts, truncated = select_excerpts(sources, max_chars=3000)

    assert truncated is True
    assert [e.section for e in excerpts] == ["Eligibility", "Procedure", "पात्रता"]


def test_the_last_relevant_section_is_cut_to_the_budget() -> None:
    sources = [source(("Overview", "y" * 5000), ("Eligibility", "z" * 5000))]

    excerpts, _ = select_excerpts(sources, max_chars=4000)

    assert [(e.section, len(e.text)) for e in excerpts] == [("Eligibility", 4000)]


def test_excerpts_hash_changes_with_any_text() -> None:
    first = [Excerpt(URL, "Eligibility", "Farmers.")]

    assert excerpts_hash(first) == excerpts_hash(list(first))
    assert excerpts_hash(first) != excerpts_hash([Excerpt(URL, "Eligibility", "Farmers!")])


def test_to_logic_builds_the_trd_tree() -> None:
    logic = to_logic(RuleExtraction.model_validate(EXTRACTION))

    assert logic == {
        "all": [
            {"field": "occupation", "op": "in", "value": ["farmer"]},
            {
                "not": {
                    "any": [
                        {"field": "is_income_tax_payer", "op": "==", "value": True},
                        {"field": "disability_percent", "op": "exists"},
                    ]
                }
            },
        ]
    }


def test_extraction_schema_only_allows_catalogue_fields() -> None:
    bad = {**EXTRACTION, "items": [{"field": "caste", "op": "==", "value": "x"}]}

    with pytest.raises(ValidationError):
        RuleExtraction.model_validate(bad)


def test_review_issues_include_refs_outside_the_excerpts() -> None:
    logic = {"all": [{"field": "age", "op": ">=", "value": 18}]}
    excerpts = [Excerpt(URL, "Eligibility", "Adults.")]

    issues = review_issues(logic, [{"url": URL, "section": "Eligibility (a)"}], excerpts)

    assert issues == [f"source ref not among the excerpts: {URL} / Eligibility (a)"]
    assert review_issues(logic, [{"url": URL, "section": "x"}], []) == []


def test_prompt_renders_fields_and_excerpts() -> None:
    spec = SchemeSpec.model_validate(
        {
            "id": "mh-scheme",
            "names": {"en": "MH Scheme"},
            "level": "state",
            "state": "MH",
            "sources": [{"url": URL}],
        }
    )
    from sahayak.eligibility.fields import PROFILE_FIELDS

    rendered = load_prompt(PROMPT_NAME).render(
        scheme=spec,
        fields=PROFILE_FIELDS.values(),
        excerpts=[Excerpt(URL, "Eligibility", "Women aged 21 to 65 <b>only</b>.")],
        truncated=True,
    )

    assert rendered.id == "rule_extraction@v1"
    assert "- `occupation` (enum: farmer, agri_labourer," in rendered.system
    assert "- `age` (int): Age in completed years" in rendered.system
    assert "Level: state, state MH" in rendered.user
    assert (
        f'<source url="{URL}" section="Eligibility">\nWomen aged 21 to 65 <b>only</b>.\n</source>'
        in rendered.user
    )
    assert "Only the sections most likely" in rendered.user
