"""The voice round-trip eval against the app served by uvicorn, with fake speech and LLM."""

from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncEngine

from sahayak.config import Settings
from sahayak.db.models import Session
from sahayak.evals.dialogues import Dialogue
from sahayak.evals.voice import VoiceEval, render_report, run_voice_eval, serve
from sahayak.voice.speech import Speaker
from sahayak.voice.tts.fake import FakeTTSProvider
from tests.agent_support import agent_llm, understood
from tests.integration.test_api_voice import Voice, _app

DIALOGUES = [
    Dialogue(
        id="d001",
        profile_id="p001",
        language="hi",
        opening="मैं किसान हूँ",
        opening_facts={"occupation": "farmer"},
    ),
    Dialogue(id="d002", profile_id="p002", language="en", opening="Hello there", opening_facts={}),
]


async def test_voice_eval_times_both_channels(
    database_url: str, settings: Settings, db_engine: AsyncEngine
) -> None:
    started = datetime.now(UTC)
    llm = agent_llm([understood("smalltalk", language="hi")] * 2 + [understood("smalltalk")] * 2)
    voice = Voice()
    voice.transcripts.extend(["मैं किसान हूँ", "मैं किसान हूँ", "Hello there", "Hello"])
    user_voice = Speaker(FakeTTSProvider(voice="user"), None, ttl_sec=1)
    seen: list[str] = []

    async with serve(_app(settings, llm, voice.services())) as base_url:
        result = await run_voice_eval(
            base_url,
            user_voice,
            DIALOGUES,
            stt_name="fake",
            on_result=lambda turn: seen.append(f"{turn.dialogue.id}:{turn.channel}"),
        )

    assert seen == ["d001:upload", "d001:stream", "d002:upload", "d002:stream"]
    assert result.errors == []
    assert all(t.first_audio_ms is not None for t in result.turns)
    assert {t.reply_language for t in result.turns} == {"hi", "en"}
    assert [t.wer for t in result.turns] == [0.0, 0.0, 0.0, 0.5]
    upload = result.stats("upload")
    assert upload["first_audio"].count == 2
    assert upload["stt"].count == 2
    assert result.stats("stream", "hi")["total"].count == 1
    assert result.passed()
    metrics = result.metrics()
    assert metrics["errors"] == 0
    assert metrics["reply_language_match"] == 1.0
    assert set(metrics["channels"]) == {"upload", "stream"}
    report = render_report(result, git_sha=None, agent_model="fake-model")
    assert "**Result:** PASS" in report
    assert "| d001 | hi | stream |" in report
    # Each turn's session was deleted afterwards.
    async with db_engine.connect() as connection:
        left = (
            await connection.execute(select(Session.id).where(Session.created_at >= started))
        ).all()
        await connection.execute(delete(Session).where(Session.created_at >= started))
    assert left == []


def test_a_failed_turn_fails_the_eval() -> None:
    from sahayak.evals.voice import VoiceTurnResult

    failed = VoiceEval(
        turns=[VoiceTurnResult(DIALOGUES[0], "upload", error="HTTP 503")], stt="x", tts="y"
    )
    assert not failed.passed()
    assert failed.metrics()["reply_language_match"] is None
    assert not VoiceEval(turns=[], stt="x", tts="y").passed()


def test_the_latency_targets_apply_to_the_stream() -> None:
    from sahayak.evals.voice import VoiceTurnResult

    def turn(channel: str, first_audio_ms: int) -> VoiceTurnResult:
        return VoiceTurnResult(
            DIALOGUES[0],
            channel,  # type: ignore[arg-type]
            transcript="x",
            audio_ok=True,
            server_ms={"total": first_audio_ms},
            first_audio_ms=first_audio_ms,
        )

    slow_upload = [turn("upload", 7000), turn("stream", 2900)]
    mixed = VoiceEval(turns=slow_upload, stt="x", tts="y")
    assert mixed.gated_channel == "stream"
    assert mixed.meets_targets("stream")
    assert not mixed.meets_targets("upload")
    assert mixed.passed()
    assert mixed.metrics()["meets_targets"] == {"stream": True, "upload": False}
    assert "upload no" in render_report(mixed, git_sha=None, agent_model="m")

    upload_only = VoiceEval(turns=[turn("upload", 7000)], stt="x", tts="y")
    assert upload_only.gated_channel == "upload"
    assert not upload_only.passed()
