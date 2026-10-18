"""Sessions, consent, text turns and the profile view (TRD §11).

A session is anonymous until accounts arrive (M10). Messages need the `profile` consent, since a
turn saves what the user says. Withdrawing a consent erases what it covered: `profile` erases the
profile, the conversation state and the message log; `sensitive` erases sensitive fields.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import delete, select

from sahayak.agent.questions import SENSITIVE_FIELDS
from sahayak.agent.replies import message
from sahayak.agent.service import AgentService, ShownScheme, TurnResult
from sahayak.agent.state import ConsentPurpose
from sahayak.api.deps import DbSession
from sahayak.api.errors import AppError, ErrorEnvelope
from sahayak.api.schemas import (
    CitationResponse,
    ConsentRecord,
    ConsentRequest,
    ConsentResponse,
    CreateSessionRequest,
    EligibilityGroups,
    FieldSourceResponse,
    MessageRequest,
    MessageResponse,
    ProfileResponse,
    SchemeResult,
    SessionResponse,
)
from sahayak.config import Settings
from sahayak.db.models import Language, Message, MessageRole, Profile, Session
from sahayak.eligibility.profile import Language as LanguageCode
from sahayak.eligibility.profile import UserProfile
from sahayak.voice.audio import AudioFormat
from sahayak.voice.pipeline import VoiceServices
from sahayak.voice.speech import spoken_text

router = APIRouter(
    prefix="/v1/sessions",
    tags=["sessions"],
    responses={
        404: {"model": ErrorEnvelope, "description": "No such session"},
        410: {"model": ErrorEnvelope, "description": "The session has expired"},
    },
)


def get_agent(request: Request) -> AgentService:
    agent: AgentService = request.app.state.agent
    return agent


Agent = Annotated[AgentService, Depends(get_agent)]


def _settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


AppSettings = Annotated[Settings, Depends(_settings)]


def get_voice(request: Request) -> VoiceServices:
    voice: VoiceServices = request.app.state.voice
    return voice


Voice = Annotated[VoiceServices, Depends(get_voice)]


def _now() -> datetime:
    return datetime.now(UTC)


_CODES: dict[Language, LanguageCode] = {Language.HI: "hi", Language.MR: "mr", Language.EN: "en"}
PURPOSES: tuple[ConsentPurpose, ...] = ("profile", "sensitive")


def session_language(session: Session) -> LanguageCode | None:
    return _CODES[Language(session.language)] if session.language else None


def _consents(session: Session) -> dict[str, ConsentRecord]:
    return {k: ConsentRecord.model_validate(v) for k, v in (session.consents or {}).items()}


def _session_response(session: Session) -> SessionResponse:
    return SessionResponse(
        session_id=session.id,
        channel=session.channel,
        language=session_language(session),
        expires_at=session.expires_at,
        consents=_consents(session),
    )


async def load_session(db: DbSession, session_id: uuid.UUID, *, lock: bool = False) -> Session:
    statement = select(Session).where(Session.id == session_id)
    if lock:  # one turn at a time per session: concurrent turns would race on the checkpoint
        statement = statement.with_for_update()
    session = await db.scalar(statement)
    if session is None:
        raise AppError("session_not_found", "No such session.", 404)
    if session.expires_at <= _now():
        raise AppError("session_expired", "This session has expired. Please start a new one.", 410)
    return session


@router.post("", status_code=201, summary="Start a session")
async def create_session(
    body: CreateSessionRequest, db: DbSession, settings: AppSettings
) -> SessionResponse:
    session = Session(
        id=uuid.uuid4(),
        channel=body.channel,
        language=Language(body.language) if body.language else None,
        consents={},
        expires_at=_now() + timedelta(days=settings.session_ttl_days),
    )
    db.add(session)
    await db.commit()
    return _session_response(session)


@router.post("/{session_id}/consent", summary="Grant or withdraw consent")
async def set_consent(
    session_id: uuid.UUID, body: ConsentRequest, db: DbSession, agent: Agent, voice: Voice
) -> ConsentResponse:
    session = await load_session(db, session_id, lock=True)
    consents: dict[str, Any] = dict(session.consents or {})
    if body.granted:
        record = {"granted_at": _now().isoformat(), "policy_version": body.policy_version}
        consents.update(dict.fromkeys(body.purposes, record))
    else:
        for purpose in body.purposes:
            consents.pop(purpose, None)
        if "profile" in body.purposes:
            await _erase_conversation(db, agent, voice, session_id)
        elif "sensitive" in body.purposes:
            await _erase_sensitive(db, agent, session_id)
    session.consents = consents
    await db.commit()
    return ConsentResponse(consents=_consents(session))


async def _erase_conversation(
    db: DbSession, agent: AgentService, voice: VoiceServices, session_id: uuid.UUID
) -> None:
    await forget_audio(db, voice, session_id)
    await db.execute(delete(Profile).where(Profile.session_id == session_id))
    await db.execute(delete(Message).where(Message.session_id == session_id))
    await agent.forget(str(session_id))


async def _erase_sensitive(db: DbSession, agent: AgentService, session_id: uuid.UUID) -> None:
    row = await db.scalar(select(Profile).where(Profile.session_id == session_id))
    if row is not None:
        row.profile = {k: v for k, v in row.profile.items() if k not in SENSITIVE_FIELDS}
        row.field_sources = {
            k: v for k, v in row.field_sources.items() if k not in SENSITIVE_FIELDS
        }
    await agent.forget_sensitive(str(session_id))


@dataclass(frozen=True)
class CompletedTurn:
    result: TurnResult
    reply_id: uuid.UUID | None  # the stored reply; None when the turn deleted the session


def require_consent(session: Session) -> list[ConsentPurpose]:
    """The session's consents; a turn needs `profile`, since it saves what the user says."""
    consents = [purpose for purpose in PURPOSES if purpose in (session.consents or {})]
    if "profile" not in consents:
        raise AppError(
            "consent_required", "Please agree to the privacy notice before chatting.", 409
        )
    return consents


async def complete_turn(
    db: DbSession,
    agent: AgentService,
    settings: Settings,
    voice: VoiceServices,
    session_id: uuid.UUID,
    text: str,
    *,
    asr_confidence: float | None = None,
) -> CompletedTurn:
    """Run one agent turn on the user's text and save it: the messages, the profile and the
    session's language and expiry. Text and voice turns both end here."""
    if not agent.available:
        raise AppError("agent_unavailable", "The assistant is not available right now.", 503)
    session = await load_session(db, session_id, lock=True)
    consents = require_consent(session)
    result = await agent.run_turn(
        str(session_id),
        text,
        language=session_language(session) or "en",
        consents=consents,
        asr_confidence=asr_confidence,
    )
    reply_id: uuid.UUID | None = _log_messages(
        db, session_id, result, asr_confidence=asr_confidence
    )
    if result.deletes_session:
        await forget_audio(db, voice, session_id)
        await agent.forget(str(session_id))
        await db.delete(session)
        reply_id = None
    else:
        if result.profile is not None:
            await _save_profile(db, session_id, result.profile)
        session.language = Language(result.language)
        session.expires_at = _now() + timedelta(days=settings.session_ttl_days)
    await db.commit()
    return CompletedTurn(result, reply_id)


async def log_notice(
    db: DbSession, session_id: uuid.UUID, key: str, language: LanguageCode
) -> tuple[str, uuid.UUID]:
    """Store a fixed assistant message that needed no agent turn ("I couldn't hear anything")
    and return its text and id."""
    text = message(key, language)
    reply_id = uuid.uuid4()
    db.add(
        Message(
            id=reply_id,
            session_id=session_id,
            role=MessageRole.ASSISTANT,
            text=text,
            lang=Language(language),
        )
    )
    await db.commit()
    return text, reply_id


def audio_url(session_id: uuid.UUID, reply_id: uuid.UUID, audio_format: AudioFormat) -> str:
    """Where the client fetches a spoken reply (synthesised on the first request)."""
    return f"/v1/sessions/{session_id}/messages/{reply_id}/audio?format={audio_format}"


@router.post(
    "/{session_id}/messages",
    summary="Send a text message",
    responses={
        409: {"model": ErrorEnvelope, "description": "Consent to save the profile is needed"},
        503: {"model": ErrorEnvelope, "description": "The assistant is not configured"},
    },
)
async def post_message(
    session_id: uuid.UUID,
    body: MessageRequest,
    db: DbSession,
    agent: Agent,
    settings: AppSettings,
    voice: Voice,
) -> MessageResponse:
    turn = await complete_turn(db, agent, settings, voice, session_id, body.text)
    url = (
        audio_url(session_id, turn.reply_id, "mp3")
        if turn.reply_id is not None and voice.speaker is not None
        else None
    )
    return message_response(turn.result, audio_url=url)


def _log_messages(
    db: DbSession, session_id: uuid.UUID, result: TurnResult, *, asr_confidence: float | None
) -> uuid.UUID:
    """Add the user's message (identifiers masked) and the reply; return the reply's id.
    Recordings are not kept: a voice message is stored as its transcript."""
    language = Language(result.language)
    reply_id = uuid.uuid4()
    db.add(
        Message(
            session_id=session_id,
            role=MessageRole.USER,
            text=result.user_text,
            lang=language,
            asr_confidence=asr_confidence,
        )
    )
    db.add(
        Message(
            id=reply_id,
            session_id=session_id,
            role=MessageRole.ASSISTANT,
            text=result.reply.text,
            lang=language,
            latency_ms=result.latency_ms,
        )
    )
    return reply_id


async def _save_profile(db: DbSession, session_id: uuid.UUID, profile: UserProfile) -> None:
    values = profile.model_dump(mode="json", exclude={"field_sources"}, exclude_none=True)
    sources = {k: v.model_dump(mode="json") for k, v in profile.field_sources.items()}
    row = await db.scalar(select(Profile).where(Profile.session_id == session_id))
    if row is None:
        db.add(Profile(session_id=session_id, profile=values, field_sources=sources))
    else:
        row.profile, row.field_sources = values, sources


def _scheme_results(shown: list[ShownScheme]) -> list[SchemeResult]:
    return [
        SchemeResult(
            scheme_id=s.scheme_id,
            name=s.name,
            provisional=s.provisional,
            reasons=s.reasons,
            official_url=s.official_url,
            last_verified_at=s.last_verified_at,
        )
        for s in shown
    ]


def message_response(result: TurnResult, *, audio_url: str | None) -> MessageResponse:
    reply = result.reply
    return MessageResponse(
        reply_text=reply.text,
        reply_audio_url=audio_url,
        kind=reply.kind,
        language=result.language,
        intent=result.intent,
        question_field=reply.question_field,
        eligibility=EligibilityGroups(
            ready=_scheme_results(result.ready),
            action_needed=_scheme_results(result.action_needed),
            provisional=_scheme_results(result.provisional),
        ),
        citations=[
            CitationResponse(
                number=c.number,
                chunk_id=c.chunk_id,
                scheme_id=c.scheme_id,
                scheme_name=result.scheme_names.get(c.scheme_id, c.scheme_id),
                section=c.section,
                source_url=c.source_url,
                last_verified_at=c.last_verified_at,
            )
            for c in reply.citations
        ],
        session_deleted=result.deletes_session,
    )


@router.get("/{session_id}/profile", summary="What Sahayak knows about the user")
async def get_profile(session_id: uuid.UUID, db: DbSession) -> ProfileResponse:
    session = await load_session(db, session_id)
    row = await db.scalar(select(Profile).where(Profile.session_id == session_id))
    return ProfileResponse(
        session_id=session_id,
        language=session_language(session),
        profile={k: v for k, v in (row.profile if row else {}).items() if k != "language"},
        field_sources={
            k: FieldSourceResponse.model_validate(v)
            for k, v in (row.field_sources if row else {}).items()
        },
    )


@router.delete(
    "/{session_id}",
    status_code=204,
    summary="Delete the session, its profile and messages",
    responses={410: {"model": ErrorEnvelope, "description": "The session has expired"}},
)
async def delete_session(
    session_id: uuid.UUID, db: DbSession, agent: Agent, voice: Voice
) -> Response:
    session = await db.scalar(select(Session).where(Session.id == session_id).with_for_update())
    if session is None:
        raise AppError("session_not_found", "No such session.", 404)
    await forget_audio(db, voice, session_id)
    await agent.forget(str(session_id))
    await db.delete(session)  # profile and messages cascade
    await db.commit()
    return Response(status_code=204)


async def forget_audio(db: DbSession, voice: VoiceServices, session_id: uuid.UUID) -> None:
    """Delete the cached audio of the session's replies: it may repeat what the user said."""
    if voice.speaker is None:
        return
    rows = await db.execute(
        select(Message.text, Message.lang).where(
            Message.session_id == session_id, Message.role == MessageRole.ASSISTANT
        )
    )
    for text, lang in rows:
        language = _CODES[Language(lang)] if lang else "en"
        await voice.speaker.forget(spoken_text(text, language), language)
