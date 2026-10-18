"""The conversation as the LLM prompts see it: the latest message and a little history."""

from dataclasses import dataclass
from typing import Literal

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

from sahayak.agent.state import AgentState
from sahayak.agent.untrusted import as_data


@dataclass(frozen=True)
class PromptMessage:
    role: Literal["user", "assistant"]
    text: str  # escaped for use inside prompt tags


def message_text(message: BaseMessage) -> str:
    return message.content if isinstance(message.content, str) else ""


def latest_user_text(state: AgentState) -> str:
    """The user's message for this turn (the API masks PII out of it first)."""
    for message in reversed(state.get("messages", [])):
        if isinstance(message, HumanMessage):
            return message_text(message)
    return ""


def history(state: AgentState, limit: int) -> list[PromptMessage]:
    """Up to `limit` messages before the latest user message, oldest first, escaped."""
    messages = list(state.get("messages", []))
    if messages and isinstance(messages[-1], HumanMessage):
        messages.pop()
    earlier = [
        PromptMessage("user" if isinstance(m, HumanMessage) else "assistant", as_data(text))
        for m in messages
        if isinstance(m, HumanMessage | AIMessage) and (text := message_text(m))
    ]
    return earlier[-limit:] if limit > 0 else []
