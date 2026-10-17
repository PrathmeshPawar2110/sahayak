from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from sahayak.db.models import SchemeLevel
from sahayak.ingestion.manifest import Manifest, load_manifest

MANIFEST = Path(__file__).parents[2] / "data" / "schemes" / "manifest.yaml"


def scheme(**overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "id": "pm-kisan",
        "names": {"en": "PM-KISAN"},
        "level": "central",
        "sources": [{"url": "https://pmkisan.gov.in/"}],
    }
    values.update(overrides)
    return values


def test_repository_manifest_has_the_first_ten_schemes() -> None:
    manifest = load_manifest(MANIFEST)

    assert len(manifest.schemes) == 10
    levels = [s.level for s in manifest.schemes]
    assert levels.count(SchemeLevel.STATE) == 2
    assert all(s.state == "MH" for s in manifest.schemes if s.level is SchemeLevel.STATE)
    assert all(s.names.hi and s.names.mr for s in manifest.schemes)


def test_repository_manifest_never_points_at_myscheme() -> None:
    """myScheme's Terms of Use forbid automated access (D-011)."""
    manifest = load_manifest(MANIFEST)

    hosts = {s.url.host for scheme in manifest.schemes for s in scheme.sources}

    assert not any(host and host.endswith("myscheme.gov.in") for host in hosts)


def test_urls_with_parentheses_survive_validation() -> None:
    manifest = load_manifest(MANIFEST)

    assert "(English).pdf" in manifest.get("pm-kisan").primary_source_url


def test_lookup_by_id() -> None:
    manifest = Manifest.model_validate({"version": 1, "schemes": [scheme()]})

    assert manifest.get("pm-kisan").names.en == "PM-KISAN"
    with pytest.raises(KeyError):
        manifest.get("missing")


@pytest.mark.parametrize(
    ("schemes", "error"),
    [
        ([scheme(), scheme()], "duplicate scheme ids"),
        ([scheme(id="PM Kisan")], "should match pattern"),
        ([scheme(level="state")], "state schemes need a state"),
        ([scheme(state="MH")], "central schemes have no state"),
        ([scheme(state="KA", level="state")], "Input should be 'MH'"),
        ([scheme(sources=[])], "at least 1 item"),
        ([scheme(sources=[{"url": "not a url"}])], "valid URL"),
        ([scheme(sources=[{"url": "https://x.gov.in", "language": "ta"}])], "'hi', 'mr' or 'en'"),
        ([scheme(colour="blue")], "Extra inputs are not permitted"),
    ],
)
def test_invalid_manifests_are_rejected(schemes: list[dict[str, Any]], error: str) -> None:
    with pytest.raises(ValidationError, match=error):
        Manifest.model_validate({"version": 1, "schemes": schemes})
