"""Browsing the scheme catalogue (TRD §11). No eligibility here: that needs a conversation."""

from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import select

from sahayak.api.deps import DbSession
from sahayak.api.errors import AppError, ErrorEnvelope
from sahayak.api.schemas import SchemeDetail, SchemeList, SchemeSummary
from sahayak.db.models import Scheme, SchemeLevel, SchemeStatus
from sahayak.eligibility.profile import Language

router = APIRouter(prefix="/v1/schemes", tags=["schemes"])

# Listed by default: what the agent may recommend (TRD §8A). Discontinued schemes can still be
# looked up by id; archived ones never.
LISTED = (SchemeStatus.ACTIVE, SchemeStatus.PROVISIONAL)

LanguageQuery = Annotated[Language, Query(description="Language of `name`")]


def _names(scheme: Scheme) -> dict[Language, str]:
    return {
        "en": scheme.name_en,
        "hi": scheme.name_hi or scheme.name_en,
        "mr": scheme.name_mr or scheme.name_en,
    }


def _summary(scheme: Scheme, language: Language) -> SchemeSummary:
    names = _names(scheme)
    return SchemeSummary(
        id=scheme.id,
        name=names[language],
        names=names,
        level=scheme.level,
        state=scheme.state,
        status=scheme.status,
        benefits=scheme.benefits,
        official_url=scheme.application_url or scheme.source_url,
        last_verified_at=scheme.last_verified_at,
    )


@router.get("", summary="List schemes")
async def list_schemes(
    db: DbSession,
    state: Annotated[
        str | None,
        Query(pattern=r"^[A-Za-z]{2}$", description="Central schemes plus this state's"),
    ] = None,
    language: LanguageQuery = "en",
) -> SchemeList:
    statement = select(Scheme).where(Scheme.status.in_(LISTED)).order_by(Scheme.id)
    if state:
        statement = statement.where(
            (Scheme.level == SchemeLevel.CENTRAL) | (Scheme.state == state.upper())
        )
    schemes = (await db.scalars(statement)).all()
    return SchemeList(schemes=[_summary(scheme, language) for scheme in schemes])


@router.get(
    "/{scheme_id}",
    summary="One scheme",
    responses={404: {"model": ErrorEnvelope, "description": "No such scheme"}},
)
async def get_scheme(scheme_id: str, db: DbSession, language: LanguageQuery = "en") -> SchemeDetail:
    scheme = await db.get(Scheme, scheme_id)
    if scheme is None or scheme.status == SchemeStatus.ARCHIVED:
        raise AppError("scheme_not_found", "No such scheme.", 404)
    return SchemeDetail(
        **_summary(scheme, language).model_dump(),
        aliases=list(scheme.aliases or []),
        ministry=scheme.ministry,
        documents_required=list(scheme.documents_required or []),
        application_url=scheme.application_url,
        source_url=scheme.source_url,
    )
