"""PII redaction for logs and traces.

This is the M0 baseline: it masks values under sensitive keys and Aadhaar-like / Indian
mobile numbers inside free text. M4-T10 replaces the regexes with the full detector set
(bank accounts, OTPs, ...) shared with the profile-save path.
"""

import re
from collections.abc import Mapping
from typing import Any

REDACTED = "[REDACTED]"

# Keys whose values must never reach logs or traces (TRD §5 Privacy, §8B sensitive details).
SENSITIVE_KEYS: frozenset[str] = frozenset(
    {
        "aadhaar",
        "aadhaar_number",
        "account_number",
        "bank_account",
        "caste_name",
        "disability",
        "disability_percent",
        "email",
        "is_minority",
        "otp",
        "password",
        "phone",
        "phone_number",
        "religion",
        "social_category",
        "token",
        "authorization",
        "api_key",
        "secret",
    }
)

# 12 digits, optionally grouped 4-4-4 with spaces or hyphens; first digit 2-9 (UIDAI format).
_AADHAAR_RE = re.compile(r"(?<!\d)[2-9]\d{3}[\s-]?\d{4}[\s-]?\d{4}(?!\d)")
# Indian mobile: optional +91 / 0 prefix, then 10 digits starting 6-9.
_PHONE_RE = re.compile(r"(?<![\d+])(?:\+91[\s-]?|0)?[6-9]\d{9}(?!\d)")


def redact_text(text: str) -> str:
    """Mask Aadhaar-like and phone numbers in free text, keeping the last 4 digits."""
    text = _AADHAAR_RE.sub(lambda m: "XXXX-XXXX-" + _digits(m.group())[-4:], text)
    return _PHONE_RE.sub(lambda m: "XXXXXX" + _digits(m.group())[-4:], text)


def redact(value: Any, key: str | None = None) -> Any:
    """Recursively redact a log value. Sensitive keys are masked whole; strings are scanned."""
    if key is not None and key.lower() in SENSITIVE_KEYS:
        return REDACTED
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, Mapping):
        return {k: redact(v, k if isinstance(k, str) else None) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return type(value)(redact(item) for item in value)
    return value


def _digits(text: str) -> str:
    return "".join(ch for ch in text if ch.isdigit())
