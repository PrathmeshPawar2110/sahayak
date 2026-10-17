"""The curated scheme manifest (`data/schemes/manifest.yaml`, TRD §8)."""

from pathlib import Path
from typing import Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator

from sahayak.db.models import Language, SchemeLevel

SLUG_PATTERN = r"^[a-z0-9]+(-[a-z0-9]+)*$"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SchemeNames(_Strict):
    en: str
    hi: str | None = None
    mr: str | None = None


class SourceSpec(_Strict):
    url: HttpUrl
    # Language of the document's text; drives the full-text config and chunk metadata.
    language: Language = Language.EN
    note: str | None = None


class SchemeSpec(_Strict):
    id: str = Field(pattern=SLUG_PATTERN, max_length=100)
    names: SchemeNames
    aliases: list[str] = []
    ministry: str | None = None
    level: SchemeLevel
    state: Literal["MH"] | None = None  # ISO 3166-2:IN; only Maharashtra is in scope (TRD §2)
    benefits: str | None = None
    application_url: HttpUrl | None = None
    sources: list[SourceSpec] = Field(min_length=1)

    @model_validator(mode="after")
    def _state_matches_level(self) -> Self:
        if self.level is SchemeLevel.STATE and self.state is None:
            raise ValueError(f"{self.id}: state schemes need a state")
        if self.level is SchemeLevel.CENTRAL and self.state is not None:
            raise ValueError(f"{self.id}: central schemes have no state")
        return self

    @property
    def primary_source_url(self) -> str:
        return str(self.sources[0].url)


class Manifest(_Strict):
    version: Literal[1]
    schemes: list[SchemeSpec]

    @model_validator(mode="after")
    def _unique_ids(self) -> Self:
        ids = [scheme.id for scheme in self.schemes]
        duplicates = {scheme_id for scheme_id in ids if ids.count(scheme_id) > 1}
        if duplicates:
            raise ValueError(f"duplicate scheme ids: {sorted(duplicates)}")
        return self

    def get(self, scheme_id: str) -> SchemeSpec:
        for scheme in self.schemes:
            if scheme.id == scheme_id:
                return scheme
        raise KeyError(scheme_id)


def load_manifest(path: Path) -> Manifest:
    return Manifest.model_validate(yaml.safe_load(path.read_text("utf-8")))
