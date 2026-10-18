from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sahayak.db.models import EvalRun
from sahayak.eligibility.engine import Verdict
from sahayak.eligibility.profile import UserProfile
from sahayak.eligibility.rules import validate_rule
from sahayak.evals.dataset import EvalProfile, write_profiles
from sahayak.evals.eligibility import record_run, run_config, run_eligibility_eval


async def test_eval_run_is_recorded(db_session: AsyncSession, tmp_path: Path) -> None:
    profiles = [
        EvalProfile(
            id="p001",
            description="adult",
            profile=UserProfile(language="en", age=30),
            expected={"apy": Verdict.ELIGIBLE},
        )
    ]
    dataset = tmp_path / "profiles.jsonl"
    write_profiles(profiles, dataset)
    rule = validate_rule(
        {
            "scheme_id": "apy",
            "version": 1,
            "status": "approved",
            "logic": {"field": "age", "op": ">=", "value": 18},
        }
    )
    result = run_eligibility_eval(profiles, {"apy": rule}, rules_source="test")

    await record_run(
        db_session,
        result,
        git_sha="a" * 40,
        config=run_config(result, dataset=dataset, min_accuracy=0.9),
    )

    run = (await db_session.scalars(select(EvalRun))).one()
    assert (run.suite, run.git_sha) == ("eligibility", "a" * 40)
    assert run.metrics["accuracy"] == 1.0
    assert run.config["rule_versions"] == {"apy": 1}
    assert run.created_at is not None
