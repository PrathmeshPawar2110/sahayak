import pytest

from sahayak.privacy.pii import PiiKind, find_pii, mask_pii


@pytest.mark.parametrize(
    ("text", "masked", "kinds"),
    [
        # Aadhaar-like: 12 digits starting 2-9, optionally grouped 4-4-4
        ("aadhaar 2345 6789 0123", "aadhaar XXXX-XXXX-0123", ["aadhaar"]),
        ("मेरा आधार २३४५६७८९०१२३ है", "मेरा आधार XXXX-XXXX-0123 है", ["aadhaar"]),
        # Phone numbers with and without prefixes
        ("call +91-9876543210", "call XXXXXX3210", ["phone"]),
        ("माझा नंबर ९८७६५४३२१० आहे", "माझा नंबर XXXXXX3210 आहे", ["phone"]),
        # Bank accounts: 9-18 digits, or grouped next to an account word
        ("acc 123456789012345", "acc XXXXXXXX2345", ["bank_account"]),
        ("my a/c no. 1234 5678 9012 34", "my a/c no. XXXXXXXX1234", ["bank_account"]),
        ("मेरा बैंक खाता 50100 2345 6789", "मेरा बैंक खाता XXXXXXXX6789", ["bank_account"]),
        # OTPs, before or after the word; masked whole
        ("my OTP is 482913", "my OTP is XXXXXX", ["otp"]),
        ("482913 is the otp", "XXXXXX is the otp", ["otp"]),
        ("ओटीपी ४८२९", "ओटीपी XXXXXX", ["otp"]),
        ("one-time password: 1234", "one-time password: XXXXXX", ["otp"]),
        # Several kinds at once, listed in detector order
        (
            "phone 9876543210, otp 1111",
            "phone XXXXXX3210, otp XXXXXX",
            ["otp", "phone"],
        ),
    ],
)
def test_mask_pii(text: str, masked: str, kinds: list[str]) -> None:
    result = mask_pii(text)
    assert result.text == masked
    assert [k.value for k in result.kinds] == kinds


@pytest.mark.parametrize(
    "text",
    [
        "I am 45 and earn 180000 rupees a year",
        "income 1,80,000",
        "2 lakh",
        "I own 2.5 acres",
        "pin code 411001",
        "applied on 2026-07-01",
        "1234 5678 9012",  # Aadhaar numbers never start with 0 or 1
        "trace 4bf92f3577b34da6a3ce929d0e0e4736",
        "request id 0af7651916cd43dd8448eb211c80319c",
        "code 123456",  # a code that isn't called an OTP or password
        "",
    ],
)
def test_ordinary_text_is_left_alone(text: str) -> None:
    assert find_pii(text) == []
    assert mask_pii(text).text == text


def test_matches_report_positions_in_the_original_text() -> None:
    (match,) = find_pii("फ़ोन ९८७६५४३२१० पर")
    assert match.kind is PiiKind.PHONE
    assert "फ़ोन ९८७६५४३२१० पर"[match.start : match.end] == "९८७६५४३२१०"
