"""Loading rules for evaluation, from the database or from exported JSON files.

The database is the source of truth: only `approved` rules reach the engine (TRD §8 step 7).
`sahayak rules export` snapshots them into `data/rules/<scheme_id>.json` so CI can run the
eligibility eval without an ingested database (see D-026). Every rule is re-validated on load.
"""

import json
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sahayak.db.models import EligibilityRule, RuleStatus
from sahayak.eligibility.rules import SchemeRule, rule_json_schema, validate_rule

RULES_DIR = Path("data/rules")
SCHEMA_FILE = "schema.json"


async def load_approved_rules(session: AsyncSession) -> dict[str, SchemeRule]:
    rows = await session.scalars(
        select(EligibilityRule)
        .where(EligibilityRule.status == RuleStatus.APPROVED)
        .order_by(EligibilityRule.scheme_id)
    )
    return {row.scheme_id: validate_rule(row.document()) for row in rows}


async def load_latest_rules(session: AsyncSession) -> dict[str, SchemeRule]:
    """Each scheme's newest draft or approved rule, issues and all: a preview for reviewers.

    Rules whose logic is invalid can't be evaluated and are left out.
    """
    rows = await session.scalars(
        select(EligibilityRule)
        .where(EligibilityRule.status.in_([RuleStatus.APPROVED, RuleStatus.DRAFT]))
        .order_by(EligibilityRule.scheme_id, EligibilityRule.version)
    )
    latest = {row.scheme_id: row for row in rows}  # ordered by version: the last one wins
    rules: dict[str, SchemeRule] = {}
    for scheme_id, row in latest.items():
        try:
            rules[scheme_id] = SchemeRule.model_validate(row.document())
        except ValueError:
            continue
    return rules


def load_rule_files(directory: Path = RULES_DIR) -> dict[str, SchemeRule]:
    rules: dict[str, SchemeRule] = {}
    for path in sorted(directory.glob("*.json")):
        if path.name == SCHEMA_FILE:
            continue
        rule = validate_rule(json.loads(path.read_text(encoding="utf-8")))
        if rule.status != "approved":
            raise ValueError(f"{path}: only approved rules are exported, found {rule.status}")
        if path.stem != rule.scheme_id:
            raise ValueError(f"{path}: file name does not match scheme_id {rule.scheme_id!r}")
        rules[rule.scheme_id] = rule
    return rules


def write_rule_files(rules: dict[str, SchemeRule], directory: Path = RULES_DIR) -> list[Path]:
    """Replace the snapshot in `directory` with `rules` (plus the JSON Schema). Returns the
    rule files written; files of schemes that no longer have an approved rule are removed."""
    directory.mkdir(parents=True, exist_ok=True)
    _write_json(directory / SCHEMA_FILE, rule_json_schema())
    written = []
    for scheme_id, rule in sorted(rules.items()):
        path = directory / f"{scheme_id}.json"
        _write_json(path, rule.to_json())
        written.append(path)
    for stale in set(directory.glob("*.json")) - set(written) - {directory / SCHEMA_FILE}:
        stale.unlink()
    return written


def _write_json(path: Path, data: object) -> None:
    path.write_bytes((json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))
