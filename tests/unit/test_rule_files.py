import json
from pathlib import Path

import pytest

from sahayak.eligibility.rules import RuleValidationError, rule_json_schema, validate_rule
from sahayak.eligibility.store import RULES_DIR, SCHEMA_FILE, load_rule_files, write_rule_files

REPO_ROOT = Path(__file__).parents[2]


def approved(scheme_id: str, minimum_age: int = 18) -> dict[str, object]:
    return {
        "scheme_id": scheme_id,
        "version": 2,
        "status": "approved",
        "logic": {"all": [{"field": "age", "op": ">=", "value": minimum_age}]},
        "source_refs": [{"url": "https://example.gov.in", "section": "Eligibility"}],
        "reviewed_by": "Reviewer",
        "reviewed_at": "2026-10-17T10:00:00Z",
    }


def test_write_then_load_round_trips(tmp_path: Path) -> None:
    rules = {s: validate_rule(approved(s)) for s in ("apy", "pm-sym")}

    written = write_rule_files(rules, tmp_path)

    assert [p.name for p in written] == ["apy.json", "pm-sym.json"]
    assert load_rule_files(tmp_path) == rules
    assert json.loads((tmp_path / SCHEMA_FILE).read_text(encoding="utf-8")) == rule_json_schema()
    assert (tmp_path / "apy.json").read_bytes().endswith(b"}\n")


def test_write_removes_rules_that_are_no_longer_approved(tmp_path: Path) -> None:
    write_rule_files({s: validate_rule(approved(s)) for s in ("apy", "pm-sym")}, tmp_path)

    write_rule_files({"apy": validate_rule(approved("apy", 21))}, tmp_path)

    assert sorted(p.name for p in tmp_path.iterdir()) == ["apy.json", SCHEMA_FILE]
    assert load_rule_files(tmp_path)["apy"].version == 2


def test_load_rejects_drafts_misnamed_and_invalid_files(tmp_path: Path) -> None:
    (tmp_path / "apy.json").write_text(json.dumps({**approved("apy"), "status": "draft"}))
    with pytest.raises(ValueError, match="only approved rules"):
        load_rule_files(tmp_path)

    (tmp_path / "apy.json").write_text(json.dumps(approved("pm-sym")))
    with pytest.raises(ValueError, match="does not match scheme_id"):
        load_rule_files(tmp_path)

    bad = approved("apy")
    bad["logic"] = {"field": "age", "op": ">=", "value": "adult"}
    (tmp_path / "apy.json").write_text(json.dumps(bad))
    with pytest.raises(RuleValidationError):
        load_rule_files(tmp_path)


def test_committed_snapshot_is_valid_and_its_schema_current() -> None:
    directory = REPO_ROOT / RULES_DIR
    rules = load_rule_files(directory)

    assert all(rule.status == "approved" for rule in rules.values())
    schema = json.loads((directory / SCHEMA_FILE).read_text(encoding="utf-8"))
    assert schema == rule_json_schema(), "run `sahayak rules export` to refresh data/rules/"
