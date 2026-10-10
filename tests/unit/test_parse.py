import pymupdf
import pytest

from sahayak.ingestion.parse import (
    DEFAULT_SECTION,
    garbled_reason,
    normalize_text,
    parse_document,
    parse_html,
    parse_pdf,
)

# ---------------------------------------------------------------- normalisation


def test_normalize_removes_invisible_characters_and_tidies_whitespace() -> None:
    raw = "PM\u200b-KISAN\u00a0\u00a0scheme\ufeff\r\n\r\n\r\n\r\n  farmers\t\tonly  \x07"

    assert normalize_text(raw) == "PM-KISAN scheme\n\nfarmers only"


def test_normalize_converts_devanagari_digits_and_unifies_nukta_forms() -> None:
    precomposed, decomposed = "\u095c", "\u0921\u093c"  # the same letter written two ways

    assert normalize_text("₹१,२०,००० आय") == "₹1,20,000 आय"  # noqa: RUF001
    # U+095C is a composition exclusion, so NFC maps both spellings to the decomposed form.
    assert normalize_text(precomposed) == normalize_text(decomposed) == decomposed


def test_normalize_removes_joiners_and_dotted_circles() -> None:
    assert normalize_text("क्\u200dष \u25ccि") == "क्ष ि"


# ---------------------------------------------------------------- garbled Devanagari (D-012)

GOOD_MARATHI = (
    "राज्यातील महिलांच्या आर्थिक स्वातंत्र्यासाठी, त्यांच्या आरोग्य आणि पोषणामध्ये सुधारणा करणे "
    "आणि कुटुंबातील त्यांची निर्णायक भूमिका मजबूत करण्यासाठी योजना सुरु करण्यास मान्यता देण्यात "
    "येत आहे. या योजनेचा लाभ विवाहित, विधवा, घटस्फोटित महिलांना मिळेल. "
) * 3
# The same text as the "Sakal Marathi" font extracts it: i-matra becomes ह, reph becomes ण.
GARBLED_MARATHI = (
    "राज्यातील महहलांच्या आर्थक स्वातंत्र्यासाठी, त्यांच्या आरोग्य आहण पोषणामध्ये सुधारणा करणे "
    "आहण कुटुंबातील त्यांची हनणायक भूहमका मजबूत करण्यासाठी योजना सुरु करण्यास मान्यता देण्यात "
    "येत आहे. या योजनेचा लाभ हववाहहत, हवधवा, घटस्फोहटत महहलांना हमळेल. "
) * 3


def test_well_formed_marathi_is_not_flagged() -> None:
    assert garbled_reason(GOOD_MARATHI) is None


def test_missing_i_matra_is_flagged() -> None:
    reason = garbled_reason(GARBLED_MARATHI)

    assert reason is not None
    assert "i-matra" in reason


def test_known_broken_font_is_flagged() -> None:
    reason = garbled_reason("महिला", ["ABCDEE+Sakal Marathi"])

    assert reason is not None
    assert "Sakal Marathi" in reason


@pytest.mark.parametrize("text", ["English only text " * 50, "", "थोडा हिंदी"])
def test_text_without_enough_devanagari_is_not_flagged(text: str) -> None:
    assert garbled_reason(text) is None


# ---------------------------------------------------------------- HTML

SCHEME_PAGE = """<!DOCTYPE html>
<html><head><title>PMUY : New Ujjwala 2.0 Connection</title></head>
<body>
<nav><a href="/">Home</a> | <a href="/about">About</a></nav>
<main>
  <h1>Pradhan Mantri Ujjwala Yojana</h1>
  <p>Clean cooking fuel for <strong>deprived households</strong> across India.</p>
  <h2>Eligibility criteria</h2>
  <ol>
    <li>The applicant must be a woman who has attained 18 years of age.</li>
    <li>There should be no other LPG connection in the same household.</li>
  </ol>
  <h2>Documents required</h2>
  <ul><li>Know Your Customer (KYC) form</li><li>Ration card</li></ul>
</main>
<footer>Copyright Ministry of Petroleum and Natural Gas</footer>
</body></html>"""


def test_html_is_split_into_sections_by_heading() -> None:
    document = parse_html(SCHEME_PAGE)

    headings = [s.heading for s in document.sections]
    assert headings == [
        "Pradhan Mantri Ujjwala Yojana",
        "Eligibility criteria",
        "Documents required",
    ]
    eligibility = document.sections[1].text
    assert "woman who has attained 18 years" in eligibility
    assert "no other LPG connection" in eligibility
    assert "**" not in document.text
    assert document.title
    assert document.garbled is False


def test_html_text_before_any_heading_goes_to_the_default_section() -> None:
    document = parse_html(
        "<html><body><article><p>"
        + "The scheme gives income support to small and marginal farmer families. " * 5
        + "</p></article></body></html>"
    )

    assert [s.heading for s in document.sections] == [DEFAULT_SECTION]


def test_html_without_main_text_warns() -> None:
    document = parse_html(b"<html><body></body></html>")

    assert document.sections == []
    assert document.warnings == ["no main text found"]


# ---------------------------------------------------------------- PDF


def make_pdf(pages: list[list[tuple[str, float, bool]]]) -> bytes:
    """Each page is a list of (text, font size, bold) lines."""
    doc = pymupdf.open()
    for lines in pages:
        page = doc.new_page()
        y = 60.0
        for text, size, bold in lines:
            page.insert_text((50, y), text, fontsize=size, fontname="hebo" if bold else "helv")
            y += size * 1.8
    data: bytes = doc.tobytes()
    doc.close()
    return data


BODY = 10.0


def body(text: str) -> tuple[str, float, bool]:
    return (text, BODY, False)


def test_pdf_headings_by_size_and_weight() -> None:
    data = make_pdf(
        [
            [
                ("PM-KISAN Operational Guidelines", 16, True),
                ("Exclusions", 12, True),
                body("Institutional land holders are excluded from the scheme."),
                body("Families paying income tax in the last assessment year are excluded."),
                ("4. Eligibility of farmer families", BODY, True),
                body("All landholding farmer families with cultivable land are eligible."),
            ]
        ]
    )

    document = parse_pdf(data)

    assert [s.heading for s in document.sections] == [
        "Exclusions",
        "4. Eligibility of farmer families",
    ]
    assert "income tax" in document.sections[0].text
    assert document.warnings == []


def test_pdf_heading_wrapped_over_two_lines_is_merged() -> None:
    data = make_pdf(
        [
            [
                ("Eligibility Criteria for Beneficiaries Under", 13, True),
                ("Pradhan Mantri Awas Yojana Gramin", 13, True),
                body("Households living in kutcha houses are eligible for assistance."),
            ]
        ]
    )

    [section] = parse_pdf(data).sections

    assert section.heading == (
        "Eligibility Criteria for Beneficiaries Under Pradhan Mantri Awas Yojana Gramin"
    )


def test_pdf_lowercase_lines_are_never_headings() -> None:
    data = make_pdf([[("Exclusions", 13, True), ("all government employees", 13, True)]])

    [section] = parse_pdf(data).sections

    assert section.heading == "Exclusions"
    assert section.text == "all government employees"


def test_pdf_running_headers_and_page_numbers_are_removed() -> None:
    pages = [
        [
            body("Government Resolution No. ABC-2024"),
            ("Benefits" if n == 1 else f"Part {n}", 12, True),
            body(f"Paragraph {n}: each eligible woman receives a monthly benefit."),
            body(str(n)),
        ]
        for n in (1, 2, 3)
    ]
    data = make_pdf(pages)

    document = parse_pdf(data)

    assert "Government Resolution No. ABC-2024" not in document.text
    assert "\n3" not in document.text
    assert document.sections[0].heading == "Benefits"


def test_pdf_without_text_layer_warns() -> None:
    doc = pymupdf.open()
    doc.new_page()
    data = doc.tobytes()
    doc.close()

    document = parse_document("pdf", data)

    assert document.sections == []
    assert "needs OCR" in document.warnings[0]
