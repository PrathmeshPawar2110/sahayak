"""PII redaction for logs and traces.

Values under sensitive keys are masked whole. Free text is scanned with the shared PII
detectors (`sahayak.privacy.pii`): Aadhaar-like, bank account, OTP and phone numbers.
"""

from collections.abc import Mapping
from typing import Any

from sahayak.privacy.pii import mask_pii

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


def redact_text(text: str) -> str:
    """Mask Aadhaar-like, bank account, OTP and phone numbers, keeping the last 4 digits."""
    return mask_pii(text).text


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
