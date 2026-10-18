import structlog

from sahayak.config import Settings
from sahayak.voice.pipeline import StageTimer, log_voice_turn, voice_services
from sahayak.voice.stt.base import FallbackSTT
from sahayak.voice.stt.sarvam import SarvamSTT


def _settings(**values: object) -> Settings:
    return Settings(_env_file=None, app_env="test", **values)  # type: ignore[arg-type]


def test_voice_services_from_settings() -> None:
    services = voice_services(
        _settings(
            sarvam_api_key="s",
            openai_api_key="o",
            stt_fallback_provider="whisper",
            max_voice_upload_mb=2,
            stream_partial_interval_sec=1.5,
        ),
        None,
    )
    assert isinstance(services.stt, FallbackSTT)
    assert services.speaker is not None
    assert services.speaker.voice_id == "sarvam:bulbul:v3:shubh"
    assert services.max_upload_bytes == 2 * 1024 * 1024
    assert services.partial_interval_sec == 1.5


def test_voice_services_leave_out_what_is_not_configured() -> None:
    with structlog.testing.capture_logs() as logs:
        services = voice_services(_settings(), None)
    assert services.stt is None
    assert services.speaker is None
    assert {e["event"] for e in logs} == {"voice_stt_disabled", "voice_tts_disabled"}
    assert isinstance(voice_services(_settings(sarvam_api_key="s"), None).stt, SarvamSTT)


def test_stage_timer_and_the_voice_turn_event() -> None:
    timer = StageTimer()
    with timer.stage("stt"):
        pass
    timer.mark("tts", timer.started)
    with structlog.testing.capture_logs() as logs:
        log_voice_turn(
            timer,
            channel="upload",
            outcome="answered",
            language="hi",
            stt_provider="sarvam",
            asr_confidence=None,
            transcript_chars=12,
            tts="ok",
        )
    (event,) = logs
    assert event["event"] == "voice_turn"
    assert event["stt_ms"] >= 0
    assert event["agent_ms"] is None
    assert event["tts_ms"] >= 0
    assert event["total_ms"] >= event["tts_ms"]
