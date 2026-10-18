"""Application settings loaded from environment variables and `.env` (TRD §15)."""

from functools import lru_cache
from typing import Annotated, Literal, Self

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]

_EMPTY_SECRET = SecretStr("")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    # --- core
    app_env: Literal["dev", "test", "prod"] = "dev"
    database_url: str = "postgresql+asyncpg://sahayak:sahayak@localhost:5432/sahayak"
    redis_url: str = "redis://localhost:6379/0"

    # --- observability (not in TRD §15; added in M0-T06)
    log_level: LogLevel = "INFO"
    log_format: Literal["json", "console"] = "json"
    # Defaults to "console" when APP_ENV=dev, otherwise "none".
    otel_traces_exporter: Literal["none", "console"] = "none"

    # --- LLM
    llm_provider: Literal["azure_openai", "openai", "anthropic"] = "azure_openai"
    azure_openai_endpoint: str = ""
    azure_openai_api_key: SecretStr = _EMPTY_SECRET
    azure_openai_deployment: str = ""
    # Not in TRD §15 (added in M1-T02/T04, see D-013).
    azure_openai_api_version: str = "2024-10-21"
    azure_openai_embedding_deployment: str = ""
    # Not in TRD §15 (added for OpenAI's own API, see D-022).
    openai_api_key: SecretStr = _EMPTY_SECRET
    openai_model: str = "gpt-4o"
    anthropic_api_key: SecretStr = _EMPTY_SECRET
    anthropic_model: str = "claude-opus-5-5"
    llm_timeout_sec: float = Field(default=120.0, gt=0)

    # --- speech
    stt_provider: Literal["sarvam", "whisper", "indicconformer"] = "sarvam"
    tts_provider: Literal["sarvam", "openai", "indic_parler"] = "sarvam"
    sarvam_api_key: SecretStr = _EMPTY_SECRET
    sarvam_stt_model: str = "saaras:v4"
    sarvam_tts_model: str = "bulbul:v3"
    sarvam_tts_voice: str = "shubh"
    # Not in TRD §15 (added in M5, see D-041 to D-044).
    stt_fallback_provider: Literal["", "sarvam", "whisper"] = ""
    tts_fallback_provider: Literal["", "sarvam", "openai"] = ""
    openai_stt_model: str = "gpt-4o-mini-transcribe"
    openai_tts_model: str = "gpt-4o-mini-tts"
    openai_tts_voice: str = "alloy"
    speech_timeout_sec: float = Field(default=20.0, gt=0)
    tts_cache_ttl_days: int = Field(default=7, gt=0)
    max_voice_upload_mb: float = Field(default=5, gt=0, le=25)
    # Voice turns transcribed with a confidence below this confirm what was heard (FR-14).
    asr_confirm_threshold: float = Field(default=0.6, ge=0, le=1)
    # Streaming voice: send a partial transcript every N seconds of new audio (0: never).
    stream_partial_interval_sec: float = Field(default=2.0, ge=0)

    # --- embeddings
    embedding_provider: Literal["local", "azure_openai"] = "local"
    embedding_model: str = "intfloat/multilingual-e5-large"
    # Not in TRD §15 (added in M1-T04, see D-013).
    embedding_cache_ttl_days: int = Field(default=30, gt=0)
    local_model_dir: str = ".local/models"

    # --- WhatsApp
    twilio_account_sid: str = ""
    twilio_auth_token: SecretStr = _EMPTY_SECRET
    twilio_whatsapp_from: str = ""

    # --- sessions and admin
    admin_api_key: SecretStr = _EMPTY_SECRET
    phone_hash_salt: SecretStr = _EMPTY_SECRET
    session_ttl_days: int = Field(default=30, gt=0)
    max_questions_per_session: int = Field(default=8, gt=0)

    # --- scheme discovery (TRD §8A)
    discovery_enabled: bool = True
    discovery_daily_cron: str = "0 6 * * *"
    discovery_pib_cron: str = "0 */6 * * *"
    discovery_states: Annotated[list[str], NoDecode] = ["MH"]
    discovery_match_auto_threshold: float = Field(default=0.90, ge=0, le=1)
    discovery_match_review_threshold: float = Field(default=0.70, ge=0, le=1)
    crawler_user_agent: str = "SahayakBot/1.0 (+contact email)"
    crawler_rate_limit_per_domain: str = Field(default="1/s", pattern=r"^\d+(\.\d+)?/[smh]$")
    review_digest_webhook_url: str = ""
    stale_after_days: int = Field(default=90, gt=0)

    # --- accounts (TRD §8B)
    auth_provider: Literal["", "entra_external_id", "firebase", "msg91"] = ""
    jwt_secret: SecretStr = _EMPTY_SECRET
    jwt_access_ttl_min: int = Field(default=15, gt=0)
    jwt_refresh_ttl_days: int = Field(default=30, gt=0)

    # --- document vault (TRD §8B)
    blob_backend: Literal["minio", "azure_blob"] = "minio"
    azure_storage_connection_string: SecretStr = _EMPTY_SECRET
    azure_key_vault_url: str = ""
    vault_kek_name: str = "sahayak-kek"
    # NFR Security: signed URLs live at most 5 minutes.
    signed_url_ttl_sec: int = Field(default=300, gt=0, le=300)
    max_upload_mb: int = Field(default=10, gt=0)
    clamav_host: str = "clamav"
    doc_extractor: Literal["vision_llm", "azure_doc_intel", "tesseract"] = "vision_llm"

    # --- result filtering (TRD §8C)
    show_provisional_schemes: bool = True
    application_window_lookahead_days: int = Field(default=60, ge=0)

    # --- reminders (TRD §8D)
    reminder_cron: str = "0 9 * * *"
    doc_expiry_offsets_days: Annotated[list[int], NoDecode] = [30, 7, 0]
    deadline_offsets_days: Annotated[list[int], NoDecode] = [14, 3]
    quiet_hours: str = Field(
        default="21:00-08:00", pattern=r"^([01]\d|2[0-3]):[0-5]\d-([01]\d|2[0-3]):[0-5]\d$"
    )
    max_reminders_per_day: int = Field(default=1, gt=0)
    sms_provider: str = ""
    sms_dlt_entity_id: str = ""
    email_provider: str = ""
    account_inactivity_delete_months: int = Field(default=24, gt=0)

    @field_validator("discovery_states", mode="before")
    @classmethod
    def _split_states(cls, value: object) -> object:
        if isinstance(value, str):
            return [part.strip().upper() for part in value.split(",") if part.strip()]
        return value

    @field_validator("doc_expiry_offsets_days", "deadline_offsets_days", mode="before")
    @classmethod
    def _split_ints(cls, value: object) -> object:
        if isinstance(value, str):
            return [int(part) for part in value.split(",") if part.strip()]
        return value

    @model_validator(mode="after")
    def _check_consistency(self) -> Self:
        if self.discovery_match_review_threshold > self.discovery_match_auto_threshold:
            msg = "DISCOVERY_MATCH_REVIEW_THRESHOLD must not exceed DISCOVERY_MATCH_AUTO_THRESHOLD"
            raise ValueError(msg)
        if "otel_traces_exporter" not in self.model_fields_set and self.app_env == "dev":
            object.__setattr__(self, "otel_traces_exporter", "console")
        return self

    @property
    def is_dev(self) -> bool:
        return self.app_env == "dev"


@lru_cache
def get_settings() -> Settings:
    """Process-wide settings. Tests construct `Settings(...)` directly instead."""
    return Settings()
