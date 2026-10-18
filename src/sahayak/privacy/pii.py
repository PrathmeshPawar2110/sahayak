"""Detectors for identifiers a citizen must never need to share (TRD §5 Privacy, §10 guardrails).

Aadhaar-like numbers, bank account numbers, OTPs and phone numbers are found in free text, in
ASCII or Devanagari digits, and masked: the last 4 digits stay visible (OTPs are masked whole).
The same detectors mask the user's message before it is stored, logged or sent to an LLM, and
redact log events (`sahayak.privacy.redaction`).

Number boundaries exclude Latin letters too, so digit runs inside identifiers such as trace ids
are left alone.
"""

import re
from dataclasses import dataclass
from enum import StrEnum


class PiiKind(StrEnum):
    OTP = "otp"
    AADHAAR = "aadhaar"
    PHONE = "phone"
    BANK_ACCOUNT = "bank_account"


@dataclass(frozen=True)
class PiiMatch:
    kind: PiiKind
    start: int
    end: int


@dataclass(frozen=True)
class MaskedText:
    text: str
    kinds: tuple[PiiKind, ...]  # each kind found, in detector order


_DEVANAGARI_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")
_EDGE = r"0-9A-Za-z"
_OTP_WORDS = r"(?:otp|o\.t\.p\.?|one[\s-]?time[\s-]?(?:password|code)|password|ओटीपी|पासवर्ड)"
_ACCOUNT_WORDS = r"(?:account|a/c|acct|khata|खाता|खाते|अकाउंट|बँक|बैंक)"

# (kind, pattern, group to mask). Earlier detectors win where matches overlap.
_DETECTORS: tuple[tuple[PiiKind, re.Pattern[str], int], ...] = (
    (PiiKind.OTP, re.compile(_OTP_WORDS + r"\D{0,20}?(?<!\d)(\d{4,8})(?!\d)", re.I), 1),
    (PiiKind.OTP, re.compile(r"(?<!\d)(\d{4,8})(?!\d)\D{0,15}?" + _OTP_WORDS, re.I), 1),
    (
        PiiKind.AADHAAR,
        re.compile(rf"(?<![{_EDGE}])[2-9]\d{{3}}[\s-]?\d{{4}}[\s-]?\d{{4}}(?![{_EDGE}])"),
        0,
    ),
    (
        PiiKind.PHONE,
        re.compile(rf"(?<![{_EDGE}+])(?:\+91[\s-]?|0)?[6-9]\d{{9}}(?![{_EDGE}])"),
        0,
    ),
    (PiiKind.BANK_ACCOUNT, re.compile(rf"(?<![{_EDGE}])\d{{9,18}}(?![{_EDGE}])"), 0),
    # Grouped digits ("1234 5678 9012 34") count as an account number next to an account word.
    (
        PiiKind.BANK_ACCOUNT,
        re.compile(_ACCOUNT_WORDS + r"\D{0,20}?(?<!\d)(\d(?:[\s-]?\d){8,17})(?!\d)", re.I),
        1,
    ),
)


def find_pii(text: str) -> list[PiiMatch]:
    """Non-overlapping matches in `text`, in reading order."""
    normalised = text.translate(_DEVANAGARI_DIGITS)  # same length: positions carry over
    found: list[PiiMatch] = []
    for kind, pattern, group in _DETECTORS:
        for match in pattern.finditer(normalised):
            start, end = match.span(group)
            if all(end <= m.start or start >= m.end for m in found):
                found.append(PiiMatch(kind, start, end))
    return sorted(found, key=lambda m: m.start)


def mask_pii(text: str) -> MaskedText:
    matches = find_pii(text)
    if not matches:
        return MaskedText(text, ())
    normalised = text.translate(_DEVANAGARI_DIGITS)
    parts: list[str] = []
    position = 0
    for match in matches:
        parts.append(text[position : match.start])
        parts.append(_mask(match.kind, normalised[match.start : match.end]))
        position = match.end
    parts.append(text[position:])
    kinds = tuple(kind for kind in PiiKind if any(m.kind is kind for m in matches))
    return MaskedText("".join(parts), kinds)


def _mask(kind: PiiKind, value: str) -> str:
    last4 = "".join(ch for ch in value if ch.isdigit())[-4:]
    match kind:
        case PiiKind.OTP:
            return "XXXXXX"
        case PiiKind.AADHAAR:
            return f"XXXX-XXXX-{last4}"
        case PiiKind.PHONE:
            return f"XXXXXX{last4}"
        case PiiKind.BANK_ACCOUNT:
            return f"XXXXXXXX{last4}"
