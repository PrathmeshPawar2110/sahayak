"""Azure OpenAI implementation of `LLMProvider` (TRD §7). The API logic is in `openai_chat.py`."""

import httpx2
import openai

from sahayak.llm.base import RetryPolicy
from sahayak.llm.openai_chat import OpenAIChatProvider


class AzureOpenAIProvider(OpenAIChatProvider):
    name = "azure_openai"

    def __init__(
        self,
        *,
        endpoint: str,
        api_key: str,
        deployment: str,
        api_version: str,
        timeout_sec: float,
        retry: RetryPolicy | None = None,
        http_client: httpx2.AsyncClient | None = None,
    ) -> None:
        # SDK retries are off: the base class owns the retry policy.
        client = openai.AsyncAzureOpenAI(
            azure_endpoint=endpoint,
            api_key=api_key,
            api_version=api_version,
            timeout=timeout_sec,
            max_retries=0,
            http_client=http_client,
        )
        # Azure addresses models by deployment name.
        super().__init__(client, deployment, retry)
