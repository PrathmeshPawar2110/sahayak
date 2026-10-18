"""Voice turns (TRD §11, FR-1, FR-10, M5-T06): `POST /v1/sessions/{id}/voice` and spoken replies.

A voice turn is speech-to-text, then the same agent turn as a text message, then speech. If the
recording can't be transcribed, the reply says so (and asks the user to type) instead of failing.
If speech synthesis fails, the reply comes back as text only (NFR Reliability). Recordings are
never stored: a voice message is saved as its transcript, identifiers masked.
"""

import uuid
from dataclasses import dataclass
from typing import Annotated

import structlog
from fastapi import APIRouter, File, Form, Query, Response, UploadFile
from sqlalchemy import select

from sahayak.agent.service import AgentService
from sahayak.api.deps import DbSession
from sahayak.api.errors import AppError, ErrorEnvelope
from sahayak.api.schemas import (
    EligibilityGroups,
    MessageResponse,
    TranscriptResponse,
    VoiceLatency,
    VoiceResponse,
)
from sahayak.api.sessions import (
    Agent,
    AppSettings,
    Voice,
    audio_url,
    complete_turn,
    load_session,
    log_notice,
    message_response,
    require_consent,
    session_language,
)
from sahayak.config import Settings
from sahayak.db.models import Language, Message, MessageRole
from sahayak.eligibility.profile import Language as LanguageCode
from sahayak.voice.audio import UPLOAD_MIME_TYPES, AudioError, AudioFormat, sniff
from sahayak.voice.pipeline import (
    Outcome,
    StageTimer,
    TTSStatus,
    VoiceServices,
    log_voice_turn,
)
from sahayak.voice.speech import spoken_text
from sahayak.voice.stt import STTError, Transcript
from sahayak.voice.tts import TTSError

log = structlog.get_logger(__name__)

router = APIRouter(
    prefix="/v1/sessions",
    tags=["voice"],
    responses={
        404: {"model": ErrorEnvelope, "description": "No such session"},
        410: {"model": ErrorEnvelope, "description": "The session has expired"},
    },
)

_CODES: dict[Language, LanguageCode] = {Language.HI: "hi", Language.MR: "mr", Language.EN: "en"}


@dataclass(frozen=True)
class VoiceTurn:
    transcript: Transcript | None
    response: MessageResponse  # without audio
    reply_id: uuid.UUID | None  # None when the turn deleted the session
    outcome: Outcome


def notice_response(text: str, language: LanguageCode, *, error: bool) -> MessageResponse:
    return MessageResponse(
        reply_text=text,
        kind="error" if error else "notice",
        language=language,
        intent=None,
        question_field=None,
        eligibility=EligibilityGroups(ready=[], action_needed=[], provisional=[]),
        citations=[],
    )


async def check_voice_session(
    db: DbSession, agent: AgentService, voice: VoiceServices, session_id: uuid.UUID
) -> LanguageCode | None:
    """Check that a voice turn can run, before any speech-to-text is paid for. Returns the
    conversation's language, the hint for speech-to-text."""
    if voice.stt is None:
        raise AppError(
            "voice_unavailable",
            "Voice messages are not available right now. Please type your message.",
            503,
        )
    if not agent.available:
        raise AppError("agent_unavailable", "The assistant is not available right now.", 503)
    session = await load_session(db, session_id)
    require_consent(session)
    hint = session_language(session)
    await db.rollback()  # don't hold a transaction open while waiting for the provider
    return hint


async def transcribe(
    voice: VoiceServices,
    audio: bytes,
    *,
    mime: str,
    language_hint: LanguageCode | None,
    timer: StageTimer,
) -> Transcript | None:
    """The transcript, or None when every provider failed."""
    if voice.stt is None:
        return None
    with timer.stage("stt"):
        try:
            return await voice.stt.transcribe(audio, mime=mime, language_hint=language_hint)
        except STTError as exc:
            log.warning("voice_stt_failed", error=type(exc).__name__)
            return None


async def answer(
    db: DbSession,
    agent: AgentService,
    settings: Settings,
    voice: VoiceServices,
    session_id: uuid.UUID,
    transcript: Transcript | None,
    language_hint: LanguageCode | None,
    *,
    timer: StageTimer,
) -> VoiceTurn:
    """The agent's turn on the transcript. Without one (nothing heard, or speech-to-text
    failed) the reply is a fixed notice asking the user to try again or type."""
    if transcript is None or transcript.empty:
        language = language_hint or "en"
        key = "audio_failed" if transcript is None else "no_speech"
        with timer.stage("agent"):
            text, reply_id = await log_notice(db, session_id, key, language)
        return VoiceTurn(
            transcript,
            notice_response(text, language, error=transcript is None),
            reply_id,
            "stt_failed" if transcript is None else "no_speech",
        )
    with timer.stage("agent"):
        turn = await complete_turn(
            db,
            agent,
            settings,
            voice,
            session_id,
            transcript.text,
            asr_confidence=transcript.confidence,
        )
    return VoiceTurn(
        transcript, message_response(turn.result, audio_url=None), turn.reply_id, "answered"
    )


def transcript_response(transcript: Transcript | None) -> TranscriptResponse | None:
    if transcript is None:
        return None
    return TranscriptResponse(
        text=transcript.text, language=transcript.language, confidence=transcript.confidence
    )


async def _read_upload(upload: UploadFile, max_bytes: int) -> tuple[bytes, str]:
    if upload.size is not None and upload.size > max_bytes:
        raise _too_large(max_bytes)
    data = await upload.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise _too_large(max_bytes)
    container = sniff(data)
    if container is None:
        raise AppError(
            "unsupported_audio",
            "The recording's format is not supported. Send WAV, MP3, OGG, WebM, MP4, FLAC or AAC.",
            415,
        )
    return data, UPLOAD_MIME_TYPES[container]


def _too_large(max_bytes: int) -> AppError:
    return AppError(
        "audio_too_large",
        f"The recording is too large (at most {max_bytes // (1024 * 1024)} MB).",
        413,
    )


@router.post(
    "/{session_id}/voice",
    summary="Send a voice message",
    responses={
        409: {"model": ErrorEnvelope, "description": "Consent to save the profile is needed"},
        413: {"model": ErrorEnvelope, "description": "The recording is too large"},
        415: {"model": ErrorEnvelope, "description": "The recording's format is not supported"},
        503: {"model": ErrorEnvelope, "description": "Voice or the assistant is not configured"},
    },
)
async def post_voice(
    session_id: uuid.UUID,
    db: DbSession,
    agent: Agent,
    settings: AppSettings,
    voice: Voice,
    audio: Annotated[
        UploadFile,
        File(description="The recording: WAV, MP3, OGG/Opus, WebM, MP4/M4A, FLAC or AAC"),
    ],
    audio_format: Annotated[AudioFormat, Form(description="Format of the spoken reply")] = "mp3",
) -> VoiceResponse:
    """Transcribe the recording, run the turn and synthesise the reply. The response carries
    the transcript, the reply text and the URL of the reply's audio (null if synthesis failed:
    the text reply still stands)."""
    timer = StageTimer()
    data, mime = await _read_upload(audio, voice.max_upload_bytes)
    hint = await check_voice_session(db, agent, voice, session_id)
    transcript = await transcribe(voice, data, mime=mime, language_hint=hint, timer=timer)
    turn = await answer(db, agent, settings, voice, session_id, transcript, hint, timer=timer)
    language = turn.response.language
    url = None
    tts_status: TTSStatus = "skipped"
    if voice.speaker is not None and turn.reply_id is not None:
        with timer.stage("tts"):
            try:
                await voice.speaker.speak(
                    spoken_text(turn.response.reply_text, language), language, audio_format
                )
            except (TTSError, AudioError) as exc:
                log.warning("voice_tts_failed", error=type(exc).__name__)
                tts_status = "failed"
            else:
                url = audio_url(session_id, turn.reply_id, audio_format)
                tts_status = "ok"
    total_ms = timer.total_ms()
    log_voice_turn(
        timer,
        channel="upload",
        outcome=turn.outcome,
        language=language,
        stt_provider=turn.transcript.provider if turn.transcript else None,
        asr_confidence=turn.transcript.confidence if turn.transcript else None,
        transcript_chars=len(turn.transcript.text) if turn.transcript else 0,
        tts=tts_status,
        total_ms=total_ms,
    )
    return VoiceResponse.model_validate(
        {
            **turn.response.model_dump(),
            "reply_audio_url": url,
            "transcript": transcript_response(turn.transcript),
            "latency": VoiceLatency(
                stt_ms=timer.ms.get("stt"),
                agent_ms=timer.ms.get("agent"),
                tts_ms=timer.ms.get("tts"),
                total_ms=total_ms,
            ),
        }
    )


@router.get(
    "/{session_id}/messages/{message_id}/audio",
    summary="A reply, spoken",
    response_class=Response,
    responses={
        200: {"content": {"audio/mpeg": {}, "audio/wav": {}, "audio/ogg": {}}},
        503: {"model": ErrorEnvelope, "description": "Speech synthesis is not available"},
    },
)
async def get_reply_audio(
    session_id: uuid.UUID,
    message_id: uuid.UUID,
    db: DbSession,
    voice: Voice,
    audio_format: Annotated[
        AudioFormat, Query(alias="format", description="mp3 or wav (web), ogg (WhatsApp)")
    ] = "mp3",
) -> Response:
    await load_session(db, session_id)
    row = await db.scalar(
        select(Message).where(
            Message.id == message_id,
            Message.session_id == session_id,
            Message.role == MessageRole.ASSISTANT,
        )
    )
    if row is None:
        raise AppError("message_not_found", "No such reply.", 404)
    if voice.speaker is None:
        raise AppError("voice_unavailable", "Spoken replies are not available right now.", 503)
    language = _CODES[Language(row.lang)] if row.lang else "en"
    try:
        spoken = await voice.speaker.speak(spoken_text(row.text, language), language, audio_format)
    except (TTSError, AudioError) as exc:
        log.warning("voice_tts_failed", error=type(exc).__name__)
        raise AppError("tts_failed", "The reply could not be spoken right now.", 503) from exc
    # Private: the audio may repeat what the user said.
    return Response(spoken.data, media_type=spoken.mime, headers={"Cache-Control": "private"})
