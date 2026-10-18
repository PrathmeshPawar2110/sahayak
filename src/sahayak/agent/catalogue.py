"""The schemes the agent knows about, with the rule it evaluates for each (TRD §8A, §10).

Which rule a scheme is evaluated with depends on its lifecycle status:

- `active`: its approved rule. Verdicts are definitive.
- `provisional`: its approved rule if it has one, otherwise its newest draft, and only while
  `SHOW_PROVISIONAL_SCHEMES` is on. Verdicts are labelled as unverified (TRD §8A).
- `discontinued` / `archived`: none. They are never recommended (FR-18).

The catalogue also finds scheme names in text (`mentions`), which the reply post-check uses to
make sure a reply names no scheme it may not (TRD §10 guardrails).
"""

import re
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable, Iterator
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from datetime import datetime

import structlog
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sahayak.db.models import EligibilityRule, RuleStatus, Scheme, SchemeLevel, SchemeStatus
from sahayak.eligibility.profile import Language
from sahayak.eligibility.rules import SchemeRule

log = structlog.get_logger(__name__)

SessionScope = Callable[[], AbstractAsyncContextManager[AsyncSession]]

# Statuses whose schemes the agent may recommend (TRD §8A).
RECOMMENDABLE = frozenset({SchemeStatus.ACTIVE, SchemeStatus.PROVISIONAL})

_DEVANAGARI = "\u0900-\u097f"
_SEPARATORS = r"[\s\-\u2013./]*"
_PARENTHETICAL = re.compile(r"\(([^)]*)\)")


@dataclass(frozen=True)
class SchemeInfo:
    id: str
    name_en: str
    level: SchemeLevel
    status: SchemeStatus
    name_hi: str | None = None
    name_mr: str | None = None
    aliases: tuple[str, ...] = ()
    state: str | None = None
    benefits: str | None = None
    official_url: str | None = None
    last_verified_at: datetime | None = None
    rule: SchemeRule | None = None

    @property
    def provisional(self) -> bool:
        return self.status is SchemeStatus.PROVISIONAL

    def name(self, language: Language) -> str:
        localised = {"hi": self.name_hi, "mr": self.name_mr}.get(language)
        return localised or self.name_en

    def names(self) -> list[str]:
        """Every way the scheme may be written: names, aliases, abbreviations, its id."""
        found = [self.name_en, self.name_hi, self.name_mr, *self.aliases, self.id]
        # "Pradhan Mantri Kisan Samman Nidhi (PM-KISAN)" -> also the name and the abbreviation
        found += [_PARENTHETICAL.sub("", self.name_en)]
        found += [m.group(1) for m in _PARENTHETICAL.finditer(self.name_en)]
        return list(dict.fromkeys(n.strip() for n in found if n and n.strip()))


def choose_rule(
    status: SchemeStatus,
    approved: SchemeRule | None,
    latest_draft: SchemeRule | None,
    *,
    show_provisional: bool,
) -> SchemeRule | None:
    """The rule the agent evaluates for a scheme with this status (see the module docstring)."""
    if status is SchemeStatus.ACTIVE:
        return approved
    if status is SchemeStatus.PROVISIONAL and show_provisional:
        return approved or latest_draft
    return None


@dataclass(frozen=True)
class _NamePattern:
    scheme_id: str
    regex: re.Pattern[str]


def _name_regex(name: str) -> re.Pattern[str] | None:
    tokens = [t for t in re.split(r"[\s\-\u2013./]+", name) if t]
    if not tokens or len("".join(tokens)) < 3:
        return None
    body = _SEPARATORS.join(re.escape(t) for t in tokens)
    if re.search(f"[{_DEVANAGARI}]", name):
        # Marathi attaches case endings to the name ("किसानसाठी"), so no boundary on the right.
        return re.compile(f"(?<![{_DEVANAGARI}]){body}")
    return re.compile(f"(?<![0-9A-Za-z]){body}(?![0-9A-Za-z])", re.IGNORECASE)


@dataclass
class Catalogue:
    schemes: dict[str, SchemeInfo]
    _patterns: list[_NamePattern] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._patterns = [
            _NamePattern(scheme.id, regex)
            for scheme in self.schemes.values()
            for name in scheme.names()
            if (regex := _name_regex(name)) is not None
        ]

    @classmethod
    def of(cls, schemes: Iterable[SchemeInfo]) -> "Catalogue":
        return cls({scheme.id: scheme for scheme in schemes})

    def __contains__(self, scheme_id: object) -> bool:
        return scheme_id in self.schemes

    def __iter__(self) -> Iterator[SchemeInfo]:
        return iter(self.schemes.values())

    def get(self, scheme_id: str) -> SchemeInfo | None:
        return self.schemes.get(scheme_id)

    def evaluable(self, state: str | None) -> list[SchemeInfo]:
        """Schemes with a rule to evaluate that may apply in `state`: central schemes, plus that
        state's schemes (every state's while the user's state is unknown)."""
        return [
            scheme
            for scheme in self.schemes.values()
            if scheme.rule is not None
            and scheme.status in RECOMMENDABLE
            and (state is None or scheme.level is SchemeLevel.CENTRAL or scheme.state == state)
        ]

    def mentions(self, text: str) -> list[str]:
        """Ids of the schemes named in `text`, in order of first mention.

        Overlapping matches go to the longest one, so "Pradhan Mantri Jan Arogya Yojana" counts
        as PM-JAY and not also as MJPJAY's alias "Jan Arogya Yojana".
        """
        matches = sorted(
            (
                (m.end() - m.start(), m.start(), m.end(), pattern.scheme_id)
                for pattern in self._patterns
                for m in pattern.regex.finditer(text)
            ),
            key=lambda match: (-match[0], match[1]),
        )
        taken: list[tuple[int, int, str]] = []
        for _length, start, end, scheme_id in matches:
            if all(end <= s or start >= e for s, e, _ in taken):
                taken.append((start, end, scheme_id))
        return list(dict.fromkeys(scheme_id for _, _, scheme_id in sorted(taken)))


class CatalogueSource(ABC):
    @abstractmethod
    async def load(self) -> Catalogue:
        """The current catalogue. Called once per turn."""


class StaticCatalogueSource(CatalogueSource):
    """A fixed catalogue, for tests and offline runs."""

    def __init__(self, catalogue: Catalogue) -> None:
        self._catalogue = catalogue

    async def load(self) -> Catalogue:
        return self._catalogue


class DbCatalogueSource(CatalogueSource):
    """Schemes and their rules from Postgres. Archived schemes are left out."""

    def __init__(self, sessions: SessionScope, *, show_provisional: bool) -> None:
        self._sessions = sessions
        self._show_provisional = show_provisional

    async def load(self) -> Catalogue:
        async with self._sessions() as session:
            schemes = (
                await session.scalars(
                    select(Scheme).where(Scheme.status != SchemeStatus.ARCHIVED).order_by(Scheme.id)
                )
            ).all()
            rows = (
                await session.scalars(
                    select(EligibilityRule)
                    .where(EligibilityRule.status.in_([RuleStatus.APPROVED, RuleStatus.DRAFT]))
                    .order_by(EligibilityRule.scheme_id, EligibilityRule.version)
                )
            ).all()
        approved: dict[str, SchemeRule] = {}
        drafts: dict[str, SchemeRule] = {}
        for row in rows:  # ordered by version: the newest draft wins
            try:
                rule = SchemeRule.model_validate(row.document())
            except ValidationError:
                log.warning("rule_unusable", scheme_id=row.scheme_id, version=row.version)
                continue
            (approved if row.status == RuleStatus.APPROVED else drafts)[row.scheme_id] = rule
        return Catalogue.of(
            SchemeInfo(
                id=scheme.id,
                name_en=scheme.name_en,
                name_hi=scheme.name_hi,
                name_mr=scheme.name_mr,
                aliases=tuple(scheme.aliases or ()),
                level=SchemeLevel(scheme.level),
                state=scheme.state,
                status=SchemeStatus(scheme.status),
                benefits=scheme.benefits,
                official_url=scheme.application_url or scheme.source_url,
                last_verified_at=scheme.last_verified_at,
                rule=choose_rule(
                    SchemeStatus(scheme.status),
                    approved.get(scheme.id),
                    drafts.get(scheme.id),
                    show_provisional=self._show_provisional,
                ),
            )
            for scheme in schemes
        )
