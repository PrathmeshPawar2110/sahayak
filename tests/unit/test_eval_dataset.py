import json
from pathlib import Path
from typing import Any

import pytest

from sahayak.eligibility.engine import Verdict
from sahayak.eligibility.profile import UserProfile
from sahayak.evals import reference
from sahayak.evals.dataset import EvalProfile, load_profiles, write_profiles
from sahayak.ingestion.manifest import load_manifest

REPO_ROOT = Path(__file__).parents[2]

E, N, Q = Verdict.ELIGIBLE, Verdict.NOT_ELIGIBLE, Verdict.NEEDS_INFO


def case(pid: str, age: int | None, **expected: Verdict) -> EvalProfile:
    return EvalProfile(
        id=pid,
        description=pid,
        profile=UserProfile(language="en", age=age),
        expected=expected,
    )


PROFILES = [
    case("p001", 30, apy=E, ssy=N),
    case("p002", 12, apy=N, ssy=E),
    case("p003", None, apy=Q, ssy=Q),
    case("p004", 17, apy=E, ssy=E),
]


def test_profiles_round_trip_without_unknown_fields(tmp_path: Path) -> None:
    path = tmp_path / "profiles.jsonl"
    write_profiles(PROFILES, path)

    assert load_profiles(path) == PROFILES
    first = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert first["profile"] == {"language": "en", "age": 30}
    assert first["label_status"] == "generated"


def test_duplicate_profile_ids_are_rejected(tmp_path: Path) -> None:
    path = tmp_path / "profiles.jsonl"
    write_profiles([PROFILES[0], PROFILES[0]], path)

    with pytest.raises(ValueError, match="duplicate profile ids: p001"):
        load_profiles(path)


@pytest.mark.parametrize(
    ("scheme_id", "fields", "verdict", "reason"),
    [
        ("ssy", {"gender": "female", "age": 9}, E, "meets: a girl; under 10 years old"),
        ("ssy", {"gender": "female", "age": 10}, N, "fails: under 10 years old"),
        ("pm-sym", {"age": 40, "annual_family_income_inr": 180000, "occupation": "artisan",
                    "is_income_tax_payer": False, "is_govt_employee": False}, E, "meets:"),
        ("pm-sym", {"age": 40, "annual_family_income_inr": 180001}, N, "fails: income"),
        ("ab-pmjay", {"age": 70, "state": "KA"}, E, "meets:"),
        ("ab-pmjay", {"age": 70, "state": "OD"}, N, "fails: lives where PM-JAY runs"),
        ("ab-pmjay", {"age": 45}, N, "fails: aged 70 or more"),
        ("ab-pmjay", {"age": 75}, Q, "unknown: lives where PM-JAY runs"),
        ("mh-majhi-ladki-bahin", {"gender": "female", "state": "MH", "age": 21,
                                  "annual_family_income_inr": 250000,
                                  "marital_status": "single"}, E, "meets:"),
        ("pm-kisan", {"land_holding_acres": 1.0, "is_govt_employee": True}, N,
         "fails: not a serving or retired government employee"),
        ("pm-kisan", {"land_holding_acres": 1.0}, Q, "unknown: not a serving"),
    ],
)  # fmt: skip
def test_reference_labels(
    scheme_id: str, fields: dict[str, Any], verdict: Verdict, reason: str
) -> None:
    found, why = reference.label(scheme_id, UserProfile(language="en", **fields))

    assert found is verdict
    assert why.startswith(reason)


def test_generator_labels_every_manifest_scheme() -> None:
    manifest = load_manifest(REPO_ROOT / "data/schemes/manifest.yaml")
    profiles = reference.generate()

    assert set(reference.SCHEMES) == {scheme.id for scheme in manifest.schemes}
    assert len(profiles) == 30
    assert [p.id for p in profiles] == [f"p{i:03d}" for i in range(1, 31)]
    for scheme_id in reference.SCHEMES:
        verdicts = {p.expected[scheme_id] for p in profiles}
        assert verdicts == {E, N, Q}, scheme_id  # each scheme sees all three verdicts


def test_committed_eval_set_matches_the_generator_until_checked() -> None:
    committed = load_profiles(REPO_ROOT / "evals/profiles.jsonl")
    generated = {p.id: p for p in reference.generate()}

    assert len(committed) >= 30
    for profile in committed:
        if profile.label_status == "generated":
            assert profile == generated[profile.id], f"regenerate: {profile.id} changed"


def test_main_refuses_to_overwrite_checked_labels(tmp_path: Path) -> None:
    path = tmp_path / "profiles.jsonl"
    assert reference.main(["--out", str(path)]) == 0
    profiles = load_profiles(path)
    profiles[0].label_status = "checked"
    write_profiles(profiles, path)

    assert reference.main(["--out", str(path)]) == 1
    assert reference.main(["--out", str(path), "--force"]) == 0
    assert load_profiles(path)[0].label_status == "generated"
