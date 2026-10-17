"""The eligibility eval set: synthetic citizen profiles with hand-checked verdicts (TRD §12.1).

`evals/profiles.jsonl` holds one `EvalProfile` per line. Labels are generated from a reference
reading of each scheme's official criteria (`sahayak.evals.reference`), then checked by a human
(M2-T08), who sets `label_status` to `checked` and corrects any label in place. After that the
file, not the generator, is the ground truth.
"""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from sahayak.eligibility.engine import Verdict
from sahayak.eligibility.profile import UserProfile

PROFILES_PATH = Path("evals/profiles.jsonl")


class EvalProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^p\d{3}$")
    description: str
    profile: UserProfile
    expected: dict[str, Verdict]  # scheme_id -> ground-truth verdict
    reasons: dict[str, str] = Field(default_factory=dict)  # scheme_id -> why, for reviewers
    label_status: Literal["generated", "checked"] = "generated"


def load_profiles(path: Path = PROFILES_PATH) -> list[EvalProfile]:
    profiles = [
        EvalProfile.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    ids = [p.id for p in profiles]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        raise ValueError(f"{path}: duplicate profile ids: {', '.join(duplicates)}")
    return profiles


def write_profiles(profiles: list[EvalProfile], path: Path = PROFILES_PATH) -> None:
    lines = [
        p.model_dump_json(exclude_none=True, exclude={"profile": {"field_sources"}})
        for p in profiles
    ]
    path.write_bytes(("\n".join(lines) + "\n").encode("utf-8"))
