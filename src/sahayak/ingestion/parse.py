"""HTML and PDF to sectioned plain text (TRD §8 step 2).

Section headings are kept because chunks carry their section (for citations) and rule extraction
looks for the eligibility section.
"""

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

import pymupdf
import trafilatura

from sahayak.ingestion.fetch import DocumentKind

DEFAULT_SECTION = "Overview"

# Characters that carry no meaning for search: zero-width space/joiners, BOM, soft hyphen,
# word joiner, and the dotted circle that PDF extraction emits for orphaned combining marks.
_INVISIBLE = dict.fromkeys(map(ord, "\u200b\u200c\u200d\u2060\ufeff\u00ad\u25cc"), None)
_DEVANAGARI_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")
_SPACES_RE = re.compile(r"[ \t\u00a0\u2000-\u200a\u202f\u205f\u3000]+")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_BLANK_LINES_RE = re.compile(r"\n{3,}")
_MD_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_MD_EMPHASIS_RE = re.compile(r"(\*\*|__)(.+?)\1")
_DEVANAGARI_RE = re.compile(r"[ऀ-ॿ]")
_NUMBERED_RE = re.compile(r"^\d+(\.\d+)*\.?\s")
_PAGE_NUMBER_RE = re.compile(r"^(page\s*)?\d{1,4}(\s*(of|/)\s*\d{1,4})?$", re.IGNORECASE)

# Legacy Marathi/Hindi fonts whose embedded Unicode map is wrong (D-012).
GARBLING_FONTS = ("sakal marathi",)
# i-matra share of Devanagari letters below which text is treated as garbled (normal: ~3-5%).
MIN_I_MATRA_SHARE = 0.01
MIN_DEVANAGARI_FOR_CHECK = 200


@dataclass(frozen=True)
class Section:
    heading: str
    text: str


@dataclass(frozen=True)
class ParsedDocument:
    title: str | None
    sections: list[Section]
    warnings: list[str] = field(default_factory=list)
    garbled: bool = False

    @property
    def text(self) -> str:
        return "\n\n".join(f"{s.heading}\n{s.text}" for s in self.sections)


def normalize_text(text: str) -> str:
    """Unicode NFC, invisible characters removed, Devanagari digits as ASCII, tidy whitespace."""
    text = unicodedata.normalize("NFC", text).translate(_INVISIBLE).translate(_DEVANAGARI_DIGITS)
    text = _CONTROL_RE.sub("", text.replace("\r\n", "\n").replace("\r", "\n"))
    lines = [_SPACES_RE.sub(" ", line).strip() for line in text.split("\n")]
    return _BLANK_LINES_RE.sub("\n\n", "\n".join(lines)).strip()


def garbled_reason(text: str, font_names: list[str] | None = None) -> str | None:
    """Why the text looks like mis-mapped Devanagari, or None if it looks fine (D-012)."""
    devanagari = _DEVANAGARI_RE.findall(text)
    if not devanagari:
        return None
    for font in font_names or []:
        if any(bad in font.lower() for bad in GARBLING_FONTS):
            return f"uses font {font!r}, whose Unicode mapping is known to be broken"
    if len(devanagari) >= MIN_DEVANAGARI_FOR_CHECK:
        share = devanagari.count("ि") / len(devanagari)
        if share < MIN_I_MATRA_SHARE:
            return f"i-matra is only {share:.1%} of Devanagari characters (normal text: ~3-5%)"
    return None


def parse_document(kind: DocumentKind, data: bytes) -> ParsedDocument:
    return parse_pdf(data) if kind == "pdf" else parse_html(data)


# ---------------------------------------------------------------------------------- HTML


def parse_html(data: bytes | str) -> ParsedDocument:
    html = data.decode("utf-8", errors="replace") if isinstance(data, bytes) else data
    markdown = trafilatura.extract(
        html,
        output_format="markdown",
        include_tables=True,
        include_links=False,
        include_images=False,
        include_comments=False,
    )
    metadata = trafilatura.extract_metadata(html)
    title = normalize_text(metadata.title) if metadata and metadata.title else None
    if not markdown:
        return ParsedDocument(title=title, sections=[], warnings=["no main text found"])

    sections = _split_markdown(normalize_text(markdown))
    reason = garbled_reason(markdown)
    warnings = [f"garbled Devanagari: {reason}"] if reason else []
    return ParsedDocument(title=title, sections=sections, warnings=warnings, garbled=bool(reason))


def _split_markdown(markdown: str) -> list[Section]:
    sections: list[Section] = []
    heading = DEFAULT_SECTION
    lines: list[str] = []
    for line in markdown.split("\n"):
        match = _MD_HEADING_RE.match(line)
        if match:
            _add_section(sections, heading, lines)
            heading, lines = _clean_inline(match[2]), []
        else:
            lines.append(_clean_inline(line))
    _add_section(sections, heading, lines)
    return sections


def _clean_inline(text: str) -> str:
    return _MD_EMPHASIS_RE.sub(r"\2", text).strip()


def _add_section(sections: list[Section], heading: str, lines: list[str]) -> None:
    body = _BLANK_LINES_RE.sub("\n\n", "\n".join(lines)).strip()
    if not body:
        return
    if sections and sections[-1].heading == heading:
        sections[-1] = Section(heading, f"{sections[-1].text}\n\n{body}")
    else:
        sections.append(Section(heading, body))


# ---------------------------------------------------------------------------------- PDF


@dataclass
class _Line:
    text: str
    size: float
    bold: bool
    page: int


def parse_pdf(data: bytes) -> ParsedDocument:
    with pymupdf.open(stream=data, filetype="pdf") as doc:
        title = (doc.metadata or {}).get("title") or None
        fonts = sorted({font[3] for page in doc for font in page.get_fonts()})
        page_count = doc.page_count
        lines = [line for page in doc for line in _page_lines(page)]

    raw_text = "\n".join(line.text for line in lines)
    if not raw_text.strip():
        return ParsedDocument(
            title=title, sections=[], warnings=["no text layer (scanned PDF?); needs OCR"]
        )

    lines = _drop_page_furniture(lines, page_count)
    body_size = _body_font_size(lines)
    sections: list[Section] = []
    heading, paragraph = DEFAULT_SECTION, []
    previous: _Line | None = None
    for line in lines:
        if not _is_heading(line, body_size):
            paragraph.append(line.text)
        elif previous is not None and not paragraph and _continues_heading(previous, line):
            heading = f"{heading} {line.text}"  # a heading wrapped onto a second line
        else:
            _add_section(sections, heading, paragraph)
            heading, paragraph = line.text, []
        previous = line
    _add_section(sections, heading, paragraph)

    reason = garbled_reason(raw_text, fonts)
    warnings = [f"garbled Devanagari: {reason}"] if reason else []
    return ParsedDocument(
        title=normalize_text(title) if title else None,
        sections=sections,
        warnings=warnings,
        garbled=bool(reason),
    )


def _page_lines(page: Any) -> list[_Line]:
    lines: list[_Line] = []
    for block in page.get_text("dict")["blocks"]:
        for raw in block.get("lines", []):
            spans = [s for s in raw["spans"] if s["text"].strip()]
            if not spans:
                continue
            # Footnote markers are small digit-only spans (superscripts); keep them out of text.
            largest = max(s["size"] for s in spans)
            spans = [
                s for s in spans if not (s["text"].strip().isdigit() and s["size"] < 0.8 * largest)
            ]
            text = normalize_text(" ".join(s["text"] for s in spans))
            if not text:
                continue
            lines.append(
                _Line(
                    text=text,
                    size=round(max(s["size"] for s in spans), 1),
                    bold=all(s["flags"] & 16 or "bold" in s["font"].lower() for s in spans),
                    page=page.number,
                )
            )
    return lines


def _drop_page_furniture(lines: list[_Line], page_count: int) -> list[_Line]:
    """Remove page numbers and running headers/footers repeated on most pages."""
    pages_by_text: dict[str, set[int]] = {}
    for line in lines:
        pages_by_text.setdefault(line.text, set()).add(line.page)
    repeated = (
        {text for text, pages in pages_by_text.items() if len(pages) > page_count / 2}
        if page_count >= 3
        else set()
    )
    return [ln for ln in lines if ln.text not in repeated and not _PAGE_NUMBER_RE.match(ln.text)]


def _continues_heading(previous: _Line, line: _Line) -> bool:
    return previous.page == line.page and previous.size == line.size and previous.bold == line.bold


def _body_font_size(lines: list[_Line]) -> float:
    weights: Counter[float] = Counter()
    for line in lines:
        weights[line.size] += len(line.text)
    return weights.most_common(1)[0][0] if weights else 0.0


def _is_heading(line: _Line, body_size: float) -> bool:
    text = line.text
    if len(text) > 120 or len(text.split()) > 14 or not any(ch.isalpha() for ch in text):
        return False
    if text[0].islower():  # continuation of a sentence or table cell, not a heading
        return False
    if text.endswith((".", ",", ";")) and not _NUMBERED_RE.match(text):
        return False
    return line.size >= body_size * 1.15 or (line.bold and line.size >= body_size)
