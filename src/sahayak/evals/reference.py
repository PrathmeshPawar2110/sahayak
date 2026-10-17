"""Generator and reference labels for the eligibility eval set v0 (M2-T07).

The labels must not come from the rules under test, so each scheme's criteria are written here
again, in plain Python, from the official source text in the manifest (read 2026-10-17). They
use three-valued logic independently of the engine: `None` is unknown.

Labelling convention (see D-028), applied to criteria the profile fields can't express:
- A further restriction that the application process checks (an LPG connection in the household,
  a kutcha house, a bank account, EPF membership...) is assumed to be met.
- An alternative route to eligibility that can't be expressed (the SECC 2011 lists for PM-JAY) is
  not counted, so only the expressible route can make the scheme ELIGIBLE.

Profiles are hand-designed personas plus boundary variants (income exactly at a threshold, ages
on either side of a limit) and profiles with missing fields. Run
`python -m sahayak.evals.reference` to regenerate `evals/profiles.jsonl`; it refuses to
overwrite labels a human has checked unless given `--force`.
"""

import argparse
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from sahayak.eligibility.engine import Verdict
from sahayak.eligibility.profile import UserProfile
from sahayak.evals.dataset import PROFILES_PATH, EvalProfile, load_profiles, write_profiles

Tri = bool | None
Criterion = tuple[str, Tri]  # (what the scheme requires, whether the profile meets it)


def _cmp(value: Any, test: Callable[[Any], bool]) -> Tri:
    return None if value is None else test(value)


def _not(value: Tri) -> Tri:
    return None if value is None else not value


def _between(value: int | None, low: int, high: int) -> Tri:
    return _cmp(value, lambda v: low <= v <= high)


UNORGANISED = {"agri_labourer", "daily_wage", "street_vendor", "artisan", "farmer", "self_employed"}
PMJAY_NOT_RUNNING = {"DL", "WB", "OD"}  # per the September 2024 PIB backgrounder


def pm_kisan(p: UserProfile) -> list[Criterion]:
    # Operational guidelines: landholding farmer families; exclusions incl. serving/retired
    # government employees and income-tax payers. Professionals, NRIs, constitutional posts and
    # pensioners >= Rs 10,000/month can't be expressed and are assumed not to apply.
    return [
        ("owns cultivable land", _cmp(p.land_holding_acres, lambda v: v > 0)),
        ("not a serving or retired government employee", _not(p.is_govt_employee)),
        ("no income tax paid", _not(p.is_income_tax_payer)),
    ]


def pm_sym(p: UserProfile) -> list[Criterion]:
    # PIB, March 2025: age 18-40, monthly income <= Rs 15,000, unorganised worker, not an income
    # taxpayer, not covered by EPF/ESIC/NPS. Monthly income is approximated by family income
    # <= Rs 1,80,000 a year; government employees are covered by NPS.
    return [
        ("aged 18 to 40", _between(p.age, 18, 40)),
        ("income <= Rs 15,000 a month", _cmp(p.annual_family_income_inr, lambda v: v <= 180000)),
        ("unorganised-sector worker", _cmp(p.occupation, lambda v: v in UNORGANISED)),
        ("not an income-tax payer", _not(p.is_income_tax_payer)),
        ("not a government employee (NPS)", _not(p.is_govt_employee)),
    ]


def ab_pmjay(p: UserProfile) -> list[Criterion]:
    # Under 70, eligibility comes from the SECC 2011 lists, which the profile can't express.
    return [
        ("aged 70 or more (cover regardless of income)", _cmp(p.age, lambda v: v >= 70)),
        (
            "lives where PM-JAY runs (not DL, WB, OD)",
            _cmp(p.state, lambda v: v not in PMJAY_NOT_RUNNING),
        ),
    ]


def pmay_g(p: UserProfile) -> list[Criterion]:
    # PIB, November 2024: houseless / kutcha households in rural areas (assumed met); excluded:
    # government employees, monthly income over Rs 15,000, income-tax payers. "Irrigated land
    # > 2.5 acres" can't be told apart from other land and is not used.
    return [
        ("rural household", _cmp(p.residence, lambda v: v == "rural")),
        ("income <= Rs 15,000 a month", _cmp(p.annual_family_income_inr, lambda v: v <= 180000)),
        ("not a government employee", _not(p.is_govt_employee)),
        ("no income tax paid", _not(p.is_income_tax_payer)),
    ]


def pmuy(p: UserProfile) -> list[Criterion]:
    # pmuy.gov.in: adult woman from a poor household (deprivation declaration); no other LPG
    # connection in the household (assumed met).
    return [
        ("a woman", _cmp(p.gender, lambda v: v == "female")),
        ("aged 18 or more", _cmp(p.age, lambda v: v >= 18)),
        ("poor household (BPL)", p.is_bpl),
    ]


def ssy(p: UserProfile) -> list[Criterion]:
    # NSI: account opened for a girl child until she attains the age of 10.
    return [
        ("a girl", _cmp(p.gender, lambda v: v == "female")),
        ("under 10 years old", _cmp(p.age, lambda v: v < 10)),
    ]


def apy(p: UserProfile) -> list[Criterion]:
    # PIB, May 2025: citizens aged 18-40; income-tax payers excluded since 1 October 2022.
    return [
        ("aged 18 to 40", _between(p.age, 18, 40)),
        ("not an income-tax payer", _not(p.is_income_tax_payer)),
    ]


def pm_vishwakarma(p: UserProfile) -> list[Criterion]:
    # PIB, September 2025: artisan in one of 18 traditional trades, self-employed, 18+, no
    # government employee in the family. Prior loans and one-member-per-family are assumed met.
    return [
        ("artisan in a traditional trade", _cmp(p.occupation, lambda v: v == "artisan")),
        ("aged 18 or more", _cmp(p.age, lambda v: v >= 18)),
        ("not in government service", _not(p.is_govt_employee)),
    ]


def mh_mjpjay(p: UserProfile) -> list[Criterion]:
    # Integrated scheme from 1 July 2024 covers all citizens of Maharashtra.
    return [("lives in Maharashtra", _cmp(p.state, lambda v: v == "MH"))]


def mh_majhi_ladki_bahin(p: UserProfile) -> list[Criterion]:
    # Pune district page (from the GR): woman resident of Maharashtra, 21-65, family income
    # <= Rs 2,50,000; married, widowed, divorced, abandoned or one unmarried woman in the
    # family, so any marital status can qualify (the one-per-family limit is assumed met).
    return [
        ("a woman", _cmp(p.gender, lambda v: v == "female")),
        ("lives in Maharashtra", _cmp(p.state, lambda v: v == "MH")),
        ("aged 21 to 65", _between(p.age, 21, 65)),
        ("family income <= Rs 2,50,000", _cmp(p.annual_family_income_inr, lambda v: v <= 250000)),
    ]


SCHEMES: dict[str, Callable[[UserProfile], list[Criterion]]] = {
    "ab-pmjay": ab_pmjay,
    "apy": apy,
    "mh-majhi-ladki-bahin": mh_majhi_ladki_bahin,
    "mh-mjpjay": mh_mjpjay,
    "pm-kisan": pm_kisan,
    "pm-sym": pm_sym,
    "pm-vishwakarma": pm_vishwakarma,
    "pmay-g": pmay_g,
    "pmuy": pmuy,
    "ssy": ssy,
}


def label(scheme_id: str, profile: UserProfile) -> tuple[Verdict, str]:
    """Reference verdict and a one-line reason a reviewer can check against the source."""
    criteria = SCHEMES[scheme_id](profile)
    failed = [text for text, met in criteria if met is False]
    unknown = [text for text, met in criteria if met is None]
    if failed:
        return Verdict.NOT_ELIGIBLE, "fails: " + "; ".join(failed)
    if unknown:
        return Verdict.NEEDS_INFO, "unknown: " + "; ".join(unknown)
    return Verdict.ELIGIBLE, "meets: " + "; ".join(text for text, _ in criteria)


# A typical complete adult; personas override what makes them distinct.
_ADULT: dict[str, Any] = {
    "language": "en",
    "state": "MH",
    "residence": "urban",
    "age": 35,
    "gender": "male",
    "marital_status": "married",
    "annual_family_income_inr": 150000,
    "occupation": "daily_wage",
    "land_holding_acres": 0.0,
    "is_bpl": False,
    "is_income_tax_payer": False,
    "is_govt_employee": False,
}
_CHILD: dict[str, Any] = {"language": "hi", "state": "MH", "residence": "urban", "gender": "female"}

# (description, profile fields). Fields set to None are left unknown on purpose.
PERSONAS: list[tuple[str, dict[str, Any]]] = [
    # Typical citizens from the TRD §3 personas.
    (
        "Marathi small farmer with 2 acres",
        {
            **_ADULT,
            "language": "mr",
            "residence": "rural",
            "age": 45,
            "occupation": "farmer",
            "land_holding_acres": 2.0,
            "annual_family_income_inr": 120000,
        },
    ),
    (
        "Hindi daily-wage worker in a UP city",
        {**_ADULT, "language": "hi", "state": "UP", "age": 32, "is_bpl": True},
    ),
    (
        "Homemaker in Pune from a BPL family",
        {
            **_ADULT,
            "language": "mr",
            "gender": "female",
            "age": 35,
            "occupation": "homemaker",
            "annual_family_income_inr": 200000,
            "is_bpl": True,
        },
    ),
    (
        "Retired government officer, 72, in Delhi",
        {
            **_ADULT,
            "language": "hi",
            "state": "DL",
            "age": 72,
            "occupation": "other",
            "annual_family_income_inr": 600000,
            "is_income_tax_payer": True,
            "is_govt_employee": True,
        },
    ),
    (
        "Widow aged 75 in Maharashtra, no income",
        {
            **_ADULT,
            "language": "mr",
            "gender": "female",
            "age": 75,
            "marital_status": "widowed",
            "occupation": "unemployed",
            "annual_family_income_inr": 0,
            "is_bpl": True,
        },
    ),
    # Sukanya Samriddhi: a girl and the age-10 boundary.
    ("Girl aged 6 (parent asking)", {**_CHILD, "age": 6}),
    ("Girl who has just turned 10, in UP", {**_CHILD, "state": "UP", "age": 10}),
    ("Girl aged 9", {**_CHILD, "age": 9}),
    # PM Vishwakarma, PM-SYM and APY: occupations and age limits.
    (
        "Rural potter aged 28",
        {
            **_ADULT,
            "residence": "rural",
            "age": 28,
            "occupation": "artisan",
            "annual_family_income_inr": 90000,
        },
    ),
    (
        "Carpenter's apprentice aged 17",
        {
            **_ADULT,
            "age": 17,
            "marital_status": "single",
            "occupation": "artisan",
            "annual_family_income_inr": 90000,
        },
    ),
    (
        "Street vendor who has just turned 18",
        {
            **_ADULT,
            "age": 18,
            "marital_status": "single",
            "occupation": "street_vendor",
            "annual_family_income_inr": 100000,
        },
    ),
    (
        "Agricultural labourer aged exactly 40",
        {**_ADULT, "residence": "rural", "age": 40, "occupation": "agri_labourer"},
    ),
    (
        "Agricultural labourer aged 41",
        {**_ADULT, "residence": "rural", "age": 41, "occupation": "agri_labourer"},
    ),
    # Income exactly at and just over the Rs 15,000 a month line.
    (
        "Rural daily-wage worker earning exactly Rs 1,80,000",
        {**_ADULT, "residence": "rural", "age": 25, "annual_family_income_inr": 180000},
    ),
    (
        "Rural daily-wage worker earning Rs 1,80,001",
        {**_ADULT, "residence": "rural", "age": 25, "annual_family_income_inr": 180001},
    ),
    # Majhi Ladki Bahin boundaries: age 21 and 65, income Rs 2,50,000.
    (
        "Unmarried woman aged 21, family income exactly Rs 2,50,000",
        {
            **_ADULT,
            "gender": "female",
            "age": 21,
            "marital_status": "single",
            "occupation": "student",
            "annual_family_income_inr": 250000,
        },
    ),
    (
        "Married woman aged 65, family income Rs 2,50,001",
        {
            **_ADULT,
            "gender": "female",
            "age": 65,
            "occupation": "homemaker",
            "annual_family_income_inr": 250001,
        },
    ),
    (
        "Married woman aged 66 in Maharashtra",
        {
            **_ADULT,
            "gender": "female",
            "age": 66,
            "occupation": "homemaker",
            "annual_family_income_inr": 100000,
        },
    ),
    # PM-KISAN exclusions.
    (
        "Farmer with 5 acres who paid income tax",
        {
            **_ADULT,
            "residence": "rural",
            "age": 50,
            "occupation": "farmer",
            "land_holding_acres": 5.0,
            "annual_family_income_inr": 900000,
            "is_income_tax_payer": True,
        },
    ),
    (
        "Government teacher who owns 3 acres",
        {
            **_ADULT,
            "residence": "rural",
            "age": 45,
            "occupation": "salaried",
            "land_holding_acres": 3.0,
            "annual_family_income_inr": 500000,
            "is_govt_employee": True,
        },
    ),
    # Missing information: what the planner has to ask for.
    (
        "Farmer in Maharashtra, nothing else known",
        {"language": "mr", "state": "MH", "occupation": "farmer"},
    ),
    ("Woman aged 30, nothing else known", {"language": "hi", "gender": "female", "age": 30}),
    (
        "Man aged 80 in UP, nothing else known",
        {"language": "hi", "state": "UP", "gender": "male", "age": 80},
    ),
    (
        "Rural artisan aged 30, income and employment unknown",
        {
            "language": "mr",
            "state": "MH",
            "residence": "rural",
            "age": 30,
            "gender": "male",
            "occupation": "artisan",
        },
    ),
    # AB PM-JAY: the age-70 boundary and states where it doesn't run.
    (
        "Woman aged exactly 70 in Odisha",
        {**_ADULT, "state": "OD", "gender": "female", "age": 70, "occupation": "homemaker"},
    ),
    (
        "Man aged exactly 70 in Karnataka",
        {**_ADULT, "state": "KA", "age": 70, "occupation": "other"},
    ),
    # Better-off and other occupations.
    (
        "Self-employed woman in Mumbai who pays income tax",
        {
            **_ADULT,
            "gender": "female",
            "age": 38,
            "occupation": "self_employed",
            "annual_family_income_inr": 400000,
            "is_income_tax_payer": True,
        },
    ),
    (
        "Private-sector salaried man aged 30",
        {**_ADULT, "age": 30, "occupation": "salaried", "annual_family_income_inr": 300000},
    ),
    (
        "College student aged 19 from a BPL family",
        {
            **_ADULT,
            "gender": "female",
            "age": 19,
            "marital_status": "single",
            "occupation": "student",
            "annual_family_income_inr": 120000,
            "is_bpl": True,
        },
    ),
    (
        "Widowed woman farmer aged 55 with 1.5 acres",
        {
            **_ADULT,
            "language": "mr",
            "residence": "rural",
            "gender": "female",
            "age": 55,
            "marital_status": "widowed",
            "occupation": "farmer",
            "land_holding_acres": 1.5,
            "annual_family_income_inr": 80000,
            "is_bpl": True,
        },
    ),
]


def generate() -> list[EvalProfile]:
    profiles = []
    for index, (description, fields) in enumerate(PERSONAS, start=1):
        profile = UserProfile.model_validate({k: v for k, v in fields.items() if v is not None})
        labels = {scheme_id: label(scheme_id, profile) for scheme_id in SCHEMES}
        profiles.append(
            EvalProfile(
                id=f"p{index:03d}",
                description=description,
                profile=profile,
                expected={s: verdict for s, (verdict, _) in labels.items()},
                reasons={s: reason for s, (_, reason) in labels.items()},
            )
        )
    return profiles


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Regenerate the eligibility eval set.")
    parser.add_argument("--out", type=Path, default=PROFILES_PATH)
    parser.add_argument("--force", action="store_true", help="overwrite checked labels")
    args = parser.parse_args(argv)
    if args.out.exists() and not args.force:
        checked = [p.id for p in load_profiles(args.out) if p.label_status == "checked"]
        if checked:
            print(f"{args.out} has {len(checked)} checked profile(s); use --force", file=sys.stderr)
            return 1
    profiles = generate()
    write_profiles(profiles, args.out)
    print(f"wrote {len(profiles)} profiles to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
