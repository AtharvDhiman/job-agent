"""PDF rendering, plus proof that the PDF actually says what we meant.

A .docx is parsed by the ATS as a document. A PDF is parsed as a *drawing that
happens to contain text*, and the two can disagree: a resume can look immaculate
on screen and still reach the recruiter's parser as reordered fragments, joined
words, or a row of boxes. The candidate never finds out. That failure is silent,
so this module refuses to treat rendering as done until the text layer has been
read back and compared -- `render_pdf` writes it, `verify_text_layer` proves it.

Every choice here is made for the parser, not the eye:

  * one column, one frame, flowables laid out top-to-bottom -- so the content
    stream order IS the reading order, with no columns for a parser to
    interleave;
  * no tables, text boxes, images, headers or footers -- the constructs that
    make extraction reorder or drop content;
  * Helvetica, a base-14 font with a WinAnsi encoding map, so pypdf and every
    ATS get the characters back rather than embedded-subset glyph ids.

Nothing here selects or writes content. It renders the markdown
`document_generator` already produced from verified facts, and adds no word of
its own -- including in the fallbacks, which stay empty rather than invent.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from xml.sax.saxutils import escape

import pypdf
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

from app.utils.text import fold

#: Roomy enough that no ATS crops the first character, tight enough that a
#: one-page resume stays one page.
_MARGIN = 0.75 * inch

_BODY_FONT = "Helvetica"
_BOLD_FONT = "Helvetica-Bold"

#: ASCII hyphen, deliberately. A U+2022 bullet is outside WinAnsi and comes back
#: from extraction as a control character -- verified against pypdf -- which is
#: the exact "looks right, extracts as garbage" failure this module exists to
#: avoid. It is also what document_generator already emits.
_BULLET_PREFIX = "- "

_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")

#: Leading/trailing punctuation, so a markdown marker ('##', '-') reduces to
#: nothing and 'PostgreSQL,' compares equal to 'PostgreSQL'. `\W` is unicode-
#: aware here, which is the point -- see _words below.
_EDGE_PUNCTUATION_RE = re.compile(r"^[\W_]+|[\W_]+$")

#: C0 and C1 control characters. Nothing legitimate survives a text extraction
#: as one of these -- `extracted` has already had every run of whitespace
#: collapsed to a single space before this pattern is applied -- so one here is
#: a byte pypdf could not map back to a character. See `_corrupt_characters`.
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]")

#: The glyphs a viewer draws where the font had nothing: a notdef box and the
#: Unicode replacement character. Both mean "this character is already lost".
_NOTDEF_CHARACTERS = frozenset("■�")

#: Below this share of the expected words surviving, the PDF is not safe to send.
#: Not 1.0: a single stray token should not condemn an otherwise clean document,
#: and `missing_words` still names it even when `ok` is True, so the caller can
#: surface the detail either way.
_MIN_EXTRACTED_RATIO = 0.98

#: A caller wants to see WHICH words vanished, not a wall of them. Past this
#: many, `extracted_ratio` is the number that matters.
_MISSING_WORDS_CAP = 25


_NAME_STYLE = ParagraphStyle("AtsName", fontName=_BOLD_FONT, fontSize=16, leading=19, spaceAfter=2)
_CONTACT_STYLE = ParagraphStyle(
    "AtsContact", fontName=_BODY_FONT, fontSize=9, leading=12, spaceAfter=10
)
_SECTION_STYLE = ParagraphStyle(
    "AtsSection", fontName=_BOLD_FONT, fontSize=12, leading=15, spaceBefore=10, spaceAfter=3
)
_SUBSECTION_STYLE = ParagraphStyle(
    "AtsSubsection", fontName=_BOLD_FONT, fontSize=10, leading=13, spaceBefore=6, spaceAfter=1
)
_BODY_STYLE = ParagraphStyle("AtsBody", fontName=_BODY_FONT, fontSize=10, leading=13, spaceAfter=2)
#: Hanging indent, not a list flowable: reportlab's ListFlowable can lay a list
#: out as a table, and a table is the thing an ATS reorders.
_BULLET_STYLE = ParagraphStyle(
    "AtsBullet",
    fontName=_BODY_FONT,
    fontSize=10,
    leading=13,
    leftIndent=12,
    firstLineIndent=-12,
    spaceAfter=2,
)


@dataclass(slots=True)
class TextLayerReport:
    """What a recruiter's parser would actually get out of the rendered PDF."""

    ok: bool = False
    #: Share of the expected body's distinct words found in the extracted layer.
    extracted_ratio: float = 0.0
    missing_words: list[str] = field(default_factory=list)
    #: Damage the word comparison cannot see, as 'U+XXXX' labels. A whole class
    #: of corruption leaves every word intact -- see `_corrupt_characters`.
    corrupt_characters: list[str] = field(default_factory=list)
    page_count: int = 0
    char_count: int = 0
    #: Set when the bytes could not be parsed at all. Empty on a clean read.
    error: str = ""

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "extracted_ratio": round(self.extracted_ratio, 3),
            "missing_words": self.missing_words,
            "corrupt_characters": self.corrupt_characters,
            "page_count": self.page_count,
            "char_count": self.char_count,
            "error": self.error,
        }


def _words(text: str) -> list[str]:
    """Comparable words, in any script.

    NOT app.utils.text.tokenize, which the rest of the repo uses for skill and
    title matching. That tokenizer requires an ASCII alphanumeric first
    character, so a name written in Chinese, Cyrillic or Devanagari yields no
    tokens at all -- and a word that never enters the expected set can never be
    reported missing. Those are precisely the words Helvetica's WinAnsi map
    cannot encode, so relying on it here would make the verifier blind to the
    one failure it exists to catch.

    `fold` is still the repo's, because both sides must fold identically for
    'María' in the source to match 'María' in the text layer.
    """
    words = (_EDGE_PUNCTUATION_RE.sub("", chunk) for chunk in fold(text).split())
    return [word for word in words if word]


def _corrupt_characters(extracted: str, expected_body: str) -> list[str]:
    """Damage the word comparison is structurally unable to see.

    `_words` trims leading and trailing non-word characters from BOTH sides, so
    every purely non-word character is deleted before anything is compared. A
    U+2022 bullet that came back from extraction as a control byte, or an emoji
    that rendered as a notdef box, therefore leaves every word present and the
    ratio at 1.0 -- the precise "looks right, extracts as garbage" outcome this
    module exists to catch, invisible to the check meant to catch it. These
    three comparisons look at characters instead of words:

      * a C0/C1 control in the extracted layer that the source never contained:
        the shape an unencodable glyph takes when pypdf reads the bytes back;
      * a notdef box or replacement character the source never contained: the
        shape the same failure takes on screen;
      * a non-space character outside ASCII present in the source and absent
        from the extracted layer: the glyph Helvetica's WinAnsi map could not
        encode, dropped rather than substituted.

    Reported as codepoint labels rather than the characters themselves. The
    offenders are unprintable by definition, and this report is written both to
    a terminal and to a JSON column.
    """
    source = set(expected_body)
    appeared = set(_CONTROL_RE.findall(extracted)) | (set(extracted) & _NOTDEF_CHARACTERS)
    vanished = {
        character
        for character in source
        if not character.isascii() and not character.isspace() and character not in extracted
    }
    offenders = (appeared - source) | vanished
    return sorted(f"U+{ord(character):04X}" for character in offenders)


def _inline(text: str) -> str:
    """Escape for reportlab's mini-markup, then honour **bold**.

    Escaping first is what keeps a literal '<' in someone's job title from being
    read as markup -- and what stops the <b> tags added below being escaped in
    turn.
    """
    return _BOLD_RE.sub(r"<b>\1</b>", escape(text))


def _flowables(markdown_body: str) -> list:
    """One flowable per source line, in source order.

    The name and contact lines carry no marker of their own -- position is the
    only signal document_generator gives us -- so they are consumed first and
    any marker closes that unmarked header block.
    """
    story: list = []
    expecting = "name"

    for raw in markdown_body.splitlines():
        line = raw.strip()
        if not line:
            # Blank lines are spacing, and the styles already own the spacing.
            continue

        if line.startswith("### "):
            story.append(Paragraph(_inline(line[4:]), _SUBSECTION_STYLE))
            expecting = ""
        elif line.startswith("## "):
            story.append(Paragraph(_inline(line[3:]), _SECTION_STYLE))
            expecting = ""
        elif line.startswith("# "):
            style = _NAME_STYLE if expecting == "name" else _SECTION_STYLE
            story.append(Paragraph(_inline(line[2:]), style))
            expecting = "contact" if expecting == "name" else ""
        elif line.startswith(("- ", "* ")):
            story.append(Paragraph(_BULLET_PREFIX + _inline(line[2:]), _BULLET_STYLE))
            expecting = ""
        elif expecting == "name":
            story.append(Paragraph(_inline(line), _NAME_STYLE))
            expecting = "contact"
        elif expecting == "contact":
            story.append(Paragraph(_inline(line), _CONTACT_STYLE))
            expecting = ""
        else:
            story.append(Paragraph(_inline(line), _BODY_STYLE))

    return story


def render_pdf(markdown_body: str, *, title: str = "") -> bytes:
    """Render document_generator's markdown as a single-column, ATS-safe PDF.

    Pair every call with `verify_text_layer` before the file reaches a recruiter.
    """
    buffer = io.BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=LETTER,
        leftMargin=_MARGIN,
        rightMargin=_MARGIN,
        topMargin=_MARGIN,
        bottomMargin=_MARGIN,
        title=title or None,
        # Named so a recruiter can see the file was machine-generated. No
        # candidate detail goes in the metadata.
        creator="jobagent",
        subject="",
    )

    story = _flowables(markdown_body)
    if not story:
        # reportlab emits a ZERO-page PDF for an empty story, and a zero-page
        # PDF breaks viewers and parsers alike. One blank page is at least a
        # readable file that verify_text_layer can report honestly on.
        story = [Spacer(1, 1)]

    # build()'s onFirstPage/onLaterPages default to drawing nothing, which is
    # how this document stays free of headers and footers.
    document.build(story)
    return buffer.getvalue()


def verify_text_layer(pdf_bytes: bytes, expected_body: str) -> TextLayerReport:
    """Read the PDF back and check the words a recruiter must see survived.

    Two comparisons, because one of them cannot see the other's failures:
    `missing_words` catches whole words the render lost, `corrupt_characters`
    catches the non-word damage that leaves every word standing. `ok` requires
    both to be clean.

    Deliberately narrow: this answers "did the text make it through the render",
    not "is this a good resume". Emptiness, thin sections and missing keywords
    are document_critic's question, so an empty body that extracts as empty is
    a faithful render and reports ok.
    """
    report = TextLayerReport()

    try:
        reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
        pages = [page.extract_text() or "" for page in reader.pages]
    except Exception as exc:  # noqa: BLE001 - last gate before a recruiter sees it
        # A parser that cannot read our own output is exactly the signal the
        # caller needs, so it comes back as a report rather than a traceback.
        report.error = f"{type(exc).__name__}: {exc}"
        return report

    report.page_count = len(pages)
    # Collapse every newline and run of spaces: line breaks are a rendering
    # artefact, and comparing on them would flag correct documents.
    extracted = " ".join(" ".join(pages).split())
    report.char_count = len(extracted)

    # Both sides go through the same folding and punctuation trim, so markdown
    # markers ('##', '-', '**') reduce to nothing on the expected side and are
    # never reported as missing.
    present = set(_words(extracted))
    expected = list(dict.fromkeys(_words(expected_body)))

    missing = [word for word in expected if word not in present]
    report.missing_words = missing[:_MISSING_WORDS_CAP]
    report.extracted_ratio = (len(expected) - len(missing)) / len(expected) if expected else 1.0

    # Non-word characters are the one class of damage the comparison above is
    # blind to, and they are checked whole rather than tokenised.
    report.corrupt_characters = _corrupt_characters(extracted, expected_body)

    report.ok = (
        report.page_count >= 1
        and report.extracted_ratio >= _MIN_EXTRACTED_RATIO
        and not report.corrupt_characters
        and (report.char_count > 0 or not expected)
    )
    return report
