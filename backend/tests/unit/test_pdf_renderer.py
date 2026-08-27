"""A PDF that looks right can still extract as garbage. These tests read it back.

Nothing here asserts on bytes. Byte output drifts with every reportlab release
and tells you nothing about what a recruiter's ATS receives, so every assertion
goes through the extracted text layer -- the same surface the ATS reads.
"""

from __future__ import annotations

import json

from app.services import document_generator, pdf_renderer
from tests.conftest import make_job


def _profile(**overrides):
    from app.models.profile import CandidateProfile

    defaults = dict(
        full_name="Dana Reed",
        headline="Backend engineer focused on Python services",
        contact_email="dana@example.com",
        location_city="Austin",
        location_region="TX",
        location_country="US",
        linkedin_url="https://www.linkedin.com/in/danareed",
        skills=["python", "postgresql"],
    )
    defaults.update(overrides)
    return CandidateProfile(**defaults)


def _facts():
    from datetime import date

    from app.models.profile import CareerFact

    return [
        CareerFact(
            category="employment",
            title="Senior Backend Engineer",
            organization="Northwind Systems",
            value="Senior Backend Engineer at Northwind Systems",
            location="Austin, TX",
            start_date=date(2021, 3, 1),
            is_current=True,
            highlights=[
                "Built a Python and PostgreSQL service used by internal teams",
                "Moved deployments to Kubernetes on AWS",
            ],
            tags=["python", "postgresql", "kubernetes", "aws"],
            verified=True,
        ),
        CareerFact(
            category="education",
            value="B.S. Computer Science, State University",
            organization="State University",
            start_date=date(2014, 9, 1),
            end_date=date(2018, 6, 1),
            verified=True,
        ),
        CareerFact(category="skill", key="python", value="python", verified=True),
        CareerFact(category="skill", key="postgresql", value="postgresql", verified=True),
    ]


def _resume_markdown() -> str:
    """Real generator output, not a hand-written approximation of it.

    The renderer's job is to survive whatever document_generator emits, so the
    markdown shapes under test have to come from document_generator itself.
    """
    return document_generator.generate_resume(_profile(), _facts(), make_job()).body


def _extract(pdf_bytes: bytes) -> str:
    import io

    import pypdf

    pages = [page.extract_text() or "" for page in pypdf.PdfReader(io.BytesIO(pdf_bytes)).pages]
    return " ".join(" ".join(pages).split())


def test_a_generated_resume_round_trips_through_the_text_layer():
    body = _resume_markdown()
    report = pdf_renderer.verify_text_layer(pdf_renderer.render_pdf(body, title="Resume"), body)

    assert report.ok
    assert report.missing_words == []
    assert report.extracted_ratio == 1.0
    assert report.page_count >= 1
    assert report.char_count > 0
    assert not report.error


def test_headings_and_bullets_survive_as_selectable_text():
    body = _resume_markdown()
    text = _extract(pdf_renderer.render_pdf(body))

    # Section headings reach the parser as their own words, not as markup.
    assert "Experience" in text
    assert "Education" in text
    assert "##" not in text and "###" not in text

    # Bullet content, and the plain ASCII marker that carries it.
    assert "Built a Python and PostgreSQL service used by internal teams" in text
    assert "- Moved deployments to Kubernetes on AWS" in text

    # The header block: name and contact details are the first thing extracted.
    assert text.startswith("Dana Reed")
    assert "dana@example.com" in text


def test_sections_extract_in_reading_order():
    """Single column, one frame: content stream order must be reading order.

    Multi-column or table layouts are exactly where extraction interleaves, so
    this is the assertion that would catch such a layout creeping back in.
    """
    body = _resume_markdown()
    text = _extract(pdf_renderer.render_pdf(body))

    assert text.index("Dana Reed") < text.index("Summary")
    assert text.index("Summary") < text.index("Experience")
    assert text.index("Experience") < text.index("Education")


def test_content_that_flows_onto_a_second_page_is_still_fully_extracted():
    lines = [f"- Delivered milestone {n} for the platform team" for n in range(120)]
    body = "Dana Reed\ndana@example.com\n\n## Experience\n" + "\n".join(lines) + "\n"

    report = pdf_renderer.verify_text_layer(pdf_renderer.render_pdf(body), body)

    assert report.page_count >= 2
    assert report.ok
    assert report.missing_words == []


def test_the_verifier_reports_words_that_did_not_survive():
    """Otherwise `ok` is a rubber stamp.

    Passing a body the PDF was never rendered from stands in for the real
    failure -- a glyph the font cannot encode -- with the same observable
    outcome: an expected word absent from the text layer.
    """
    pdf_bytes = pdf_renderer.render_pdf("Dana Reed\ndana@example.com\n")
    report = pdf_renderer.verify_text_layer(pdf_bytes, "Dana Reed ran the Zanzibar migration")

    assert not report.ok
    assert "zanzibar" in report.missing_words
    assert "migration" in report.missing_words
    assert report.extracted_ratio < 1.0
    # The words that DID survive are not reported as missing.
    assert "dana" not in report.missing_words


def test_a_glyph_the_font_cannot_encode_is_caught_rather_than_shipped():
    """The headline reason PDFs are risky, reproduced end to end.

    Helvetica's WinAnsi map has no CJK, so reportlab draws boxes and the text
    layer loses the word. The page still LOOKS like a resume. Only reading the
    layer back catches it -- which is the whole argument for this module.
    """
    body = "Dana Reed\ndana@example.com\n\n## Experience\n- Led the 中文 localisation\n"
    report = pdf_renderer.verify_text_layer(pdf_renderer.render_pdf(body), body)

    assert not report.ok
    assert "中文" in report.missing_words
    assert "localisation" not in report.missing_words


def test_a_bullet_that_extracts_as_a_control_character_is_caught():
    """The failure the word comparison structurally cannot see.

    A U+2022 bullet comes back from extraction as a control byte. Every WORD
    survives, so `missing_words` is empty and the ratio is a perfect 1.0 -- the
    verifier's own headline numbers say the document is clean while the text
    layer already contains garbage a recruiter's parser will read.
    """
    body = "Dana Reed\ndana@example.com\n\n## Experience\n• Owned the migration\n"
    report = pdf_renderer.verify_text_layer(pdf_renderer.render_pdf(body), body)

    assert report.missing_words == []
    assert report.extracted_ratio == 1.0
    # ...and yet it is not ok, because of what the characters say.
    assert not report.ok
    assert "U+2022" in report.corrupt_characters  # the bullet never arrived
    assert "U+007F" in report.corrupt_characters  # what arrived instead


def test_a_glyph_that_renders_as_a_notdef_box_is_caught():
    """An emoji draws a visible black box and loses no word at all."""
    body = "Dana Reed\ndana@example.com\n\n## Experience\n- Shipped \U0001f680 on time\n"
    report = pdf_renderer.verify_text_layer(pdf_renderer.render_pdf(body), body)

    assert report.missing_words == []
    assert not report.ok
    assert "U+1F680" in report.corrupt_characters
    assert "U+25A0" in report.corrupt_characters


def test_corrupt_characters_are_reported_as_labels_not_as_the_characters():
    """A report is printed to a terminal and stored in a JSON column.

    The offending characters are unprintable by definition, so naming them by
    codepoint is what keeps the report itself readable and encodable.
    """
    body = "Dana Reed\ndana@example.com\n\n## Experience\n• Owned it\n"
    report = pdf_renderer.verify_text_layer(pdf_renderer.render_pdf(body), body)

    assert report.corrupt_characters
    for label in report.corrupt_characters:
        assert label.startswith("U+")
        assert label.encode("ascii")


def test_accented_latin_names_round_trip_intact():
    """The common case that must NOT be flagged: WinAnsi covers Latin-1."""
    body = "María González\nmaria@example.com\n\n## Experience\n- Built résumé tooling\n"
    report = pdf_renderer.verify_text_layer(pdf_renderer.render_pdf(body), body)

    assert report.ok
    assert report.missing_words == []
    assert "María González" in _extract(pdf_renderer.render_pdf(body))


def test_an_empty_body_produces_a_readable_file_not_a_zero_page_pdf():
    """reportlab emits a zero-page PDF for an empty story; viewers choke on it.

    Emptiness is document_critic's complaint, not the verifier's -- an empty
    body that extracts as empty is a faithful render.
    """
    pdf_bytes = pdf_renderer.render_pdf("")
    report = pdf_renderer.verify_text_layer(pdf_bytes, "")

    assert pdf_bytes.startswith(b"%PDF")
    assert report.page_count == 1
    assert report.char_count == 0
    assert report.extracted_ratio == 1.0
    assert report.ok
    assert not report.error


def test_whitespace_only_body_is_treated_like_an_empty_one():
    report = pdf_renderer.verify_text_layer(pdf_renderer.render_pdf("\n   \n\n"), "\n   \n\n")

    assert report.page_count == 1
    assert report.ok


def test_markup_characters_in_stored_strings_are_escaped_not_executed():
    """A '<' in a stored job title must reach the parser, not reportlab's markup."""
    body = "Dana Reed\ndana@example.com\n\n## Experience\n- Cut p95 latency to <200ms for R&D\n"
    text = _extract(pdf_renderer.render_pdf(body))

    assert "<200ms" in text
    assert "R&D" in text


def test_bold_markers_render_as_weight_and_leave_no_asterisks_behind():
    body = "Dana Reed\ndana@example.com\n\n## Skills\n**Python**, PostgreSQL\n"
    text = _extract(pdf_renderer.render_pdf(body))

    assert "Python" in text
    assert "*" not in text


def test_the_page_carries_nothing_an_ats_parser_can_trip_over():
    """The ATS-safety claim, asserted structurally rather than trusted.

    An image, an annotation or a form XObject is what turns extraction into
    reordered fragments, so their absence is checked on the object graph. Only
    the two base-14 Helvetica faces may appear -- an embedded subset is where
    glyph ids stop mapping back to characters.
    """
    import io

    import pypdf

    reader = pypdf.PdfReader(io.BytesIO(pdf_renderer.render_pdf(_resume_markdown())))
    page = reader.pages[0]
    resources = page.get("/Resources", {})

    assert resources.get("/XObject") is None  # no images, no form XObjects
    assert page.get("/Annots") is None
    fonts = {str(f.get_object().get("/BaseFont")) for f in resources.get("/Font", {}).values()}
    assert fonts <= {"/Helvetica", "/Helvetica-Bold"}


def test_metadata_carries_the_title_and_no_candidate_detail():
    reader_title = "Resume - Senior Backend Engineer"
    import io

    import pypdf

    body = _resume_markdown()
    reader = pypdf.PdfReader(io.BytesIO(pdf_renderer.render_pdf(body, title=reader_title)))

    assert reader.metadata.title == reader_title
    assert reader.metadata.creator == "jobagent"
    # The candidate's name reaches the page, never the document properties.
    assert "Dana Reed" not in str(dict(reader.metadata))


def test_the_report_serialises_like_the_other_report_dataclasses():
    body = _resume_markdown()
    report = pdf_renderer.verify_text_layer(pdf_renderer.render_pdf(body), body)
    payload = report.as_dict()

    assert set(payload) == {
        "ok",
        "extracted_ratio",
        "missing_words",
        "corrupt_characters",
        "page_count",
        "char_count",
        "error",
    }
    assert payload["ok"] is True
    assert isinstance(payload["missing_words"], list)
    assert payload["corrupt_characters"] == []
    # It has to survive a JSON column, like Application.critique does.
    assert json.loads(json.dumps(payload)) == payload


def test_unreadable_bytes_come_back_as_a_report_not_a_traceback():
    report = pdf_renderer.verify_text_layer(b"this is not a pdf", "Dana Reed")

    assert not report.ok
    assert report.error
    assert report.page_count == 0


def test_the_missing_word_list_is_capped_but_the_ratio_is_not():
    absent = " ".join(f"zzz{n}" for n in range(80))
    report = pdf_renderer.verify_text_layer(pdf_renderer.render_pdf("Dana Reed\n"), absent)

    assert len(report.missing_words) == pdf_renderer._MISSING_WORDS_CAP
    assert report.extracted_ratio == 0.0
    assert not report.ok
