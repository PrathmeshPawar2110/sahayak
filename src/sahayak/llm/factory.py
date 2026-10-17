"""Build the configured `LLMProvider`. Vendor SDKs are imported only for the chosen provider."""

from sahayak.config import Settings
from sahayak.llm.base import LLMError, LLMProvider


class LLMConfigError(LLMError):
    """Required settings for the selected provider are missing."""


def create_llm_provider(settings: Settings) -> LLMProvider:
    if settings.llm_provider == "anthropic":
        if not settings.anthropic_api_key.get_secret_value():
            raise LLMConfigError("LLM_PROVIDER=anthropic needs ANTHROPIC_API_KEY")
        if not settings.anthropic_model:
            raise LLMConfigError("LLM_PROVIDER=anthropic needs ANTHROPIC_MODEL")
        from sahayak.llm.anthropic import AnthropicProvider

        return AnthropicProvider(
            api_key=settings.anthropic_api_key.get_secret_value(),
            model=settings.anthropic_model,
            timeout_sec=settings.llm_timeout_sec,
        )

    if settings.llm_provider == "openai":
        if not settings.openai_api_key.get_secret_value():
            raise LLMConfigError("LLM_PROVIDER=openai needs OPENAI_API_KEY")
        if not settings.openai_model:
            raise LLMConfigError("LLM_PROVIDER=openai needs OPENAI_MODEL")
        from sahayak.llm.openai_chat import OpenAIProvider

        return OpenAIProvider(
            api_key=settings.openai_api_key.get_secret_value(),
            model=settings.openai_model,
            timeout_sec=settings.llm_timeout_sec,
        )

    missing = [
        name
        for name, value in (
            ("AZURE_OPENAI_ENDPOINT", settings.azure_openai_endpoint),
            ("AZURE_OPENAI_API_KEY", settings.azure_openai_api_key.get_secret_value()),
            ("AZURE_OPENAI_DEPLOYMENT", settings.azure_openai_deployment),
        )
        if not value
    ]
    if missing:
        raise LLMConfigError(f"LLM_PROVIDER=azure_openai needs {', '.join(missing)}")
    from sahayak.llm.azure_openai import AzureOpenAIProvider

    return AzureOpenAIProvider(
        endpoint=settings.azure_openai_endpoint,
        api_key=settings.azure_openai_api_key.get_secret_value(),
        deployment=settings.azure_openai_deployment,
        api_version=settings.azure_openai_api_version,
        timeout_sec=settings.llm_timeout_sec,
    )
