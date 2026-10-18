"""Untrusted text in prompts (TRD §10 guardrails: prompt injection).

User messages and retrieved scheme passages go into prompts inside XML-like tags, and every
prompt says the contents are data, never instructions. Escaping `<`, `>` and `&` stops such
text from closing its tag and posing as instructions or as a different speaker.
"""

from html import escape


def as_data(text: str) -> str:
    """`text` made safe to place between tags in a prompt."""
    return escape(text, quote=False)


def as_attribute(text: str) -> str:
    """`text` made safe to place inside a double-quoted tag attribute."""
    return escape(text, quote=True)
