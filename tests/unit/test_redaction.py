import pytest

from sahayak.privacy.redaction import REDACTED, redact, redact_text


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("my aadhaar is 2345 6789 0123", "my aadhaar is XXXX-XXXX-0123"),
        ("aadhaar 2345-6789-0123.", "aadhaar XXXX-XXXX-0123."),
        ("aadhaar 234567890123", "aadhaar XXXX-XXXX-0123"),
        ("call 9876543210 now", "call XXXXXX3210 now"),
        ("call +91 9876543210", "call XXXXXX3210"),
        ("call 09876543210", "call XXXXXX3210"),
    ],
)
def test_redact_text_masks_identifiers_keeping_last_four(text: str, expected: str) -> None:
    assert redact_text(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "income is 180000 rupees",
        "applied on 2026-07-01",
        "1234 5678 9012",  # Aadhaar numbers never start with 0 or 1
        "order 12345678901234",  # longer digit runs are not phone or Aadhaar numbers
    ],
)
def test_redact_text_leaves_ordinary_numbers(text: str) -> None:
    assert redact_text(text) == text


def test_redact_masks_sensitive_keys_recursively() -> None:
    event = {
        "event": "profile updated",
        "profile": {"age": 34, "religion": "x", "social_category": "obc"},
        "items": [{"phone": "9876543210"}, "note 9876543210"],
        "OTP": "123456",
    }

    assert redact(event) == {
        "event": "profile updated",
        "profile": {"age": 34, "religion": REDACTED, "social_category": REDACTED},
        "items": [{"phone": REDACTED}, "note XXXXXX3210"],
        "OTP": REDACTED,
    }


def test_redact_preserves_non_string_values() -> None:
    assert redact((1, 2.5, None, True)) == (1, 2.5, None, True)
