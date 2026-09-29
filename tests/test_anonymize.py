import pytest

pypdf = pytest.importorskip("pypdf")

from pypdf import PdfWriter

from peer_review.anonymize import anonymize_copy


def make_pdf_with_metadata(path, metadata):
    w = PdfWriter()
    w.add_blank_page(width=200, height=200)
    w.add_metadata(metadata)
    with open(path, "wb") as f:
        w.write(f)


def make_multipage_pdf(path, page_widths):
    """Each page gets a distinct width, so which physical pages survived
    stripping can be checked without needing to draw/extract text."""
    w = PdfWriter()
    for width in page_widths:
        w.add_blank_page(width=width, height=200)
    with open(path, "wb") as f:
        w.write(f)


def test_strips_identifying_pdf_metadata(tmp_path):
    source = tmp_path / "ada_lovelace_hw3.pdf"
    make_pdf_with_metadata(
        source,
        {"/Author": "Ada Lovelace", "/Creator": "Ada Lovelace - Word", "/Title": "Ada_Lovelace_HW3"},
    )

    dest_dir = tmp_path / "anon"
    dest_dir.mkdir()
    result = anonymize_copy(str(source), str(dest_dir), "peer_submission.pdf")

    assert result.metadata_stripped
    assert result.warning is None
    assert result.path.endswith("peer_submission.pdf")

    from pypdf import PdfReader

    metadata = dict(PdfReader(result.path).metadata or {})
    assert "Ada Lovelace" not in str(metadata)
    assert "/Author" not in metadata
    assert "/Title" not in metadata


def test_display_name_replaces_original_filename(tmp_path):
    source = tmp_path / "ada_lovelace_hw3.pdf"
    make_pdf_with_metadata(source, {})
    dest_dir = tmp_path / "anon"
    dest_dir.mkdir()

    result = anonymize_copy(str(source), str(dest_dir), "peer_submission.pdf")

    assert "ada_lovelace" not in result.path.lower()
    assert result.path.endswith("peer_submission.pdf")


def test_non_pdf_is_copied_but_flagged(tmp_path):
    source = tmp_path / "ada_lovelace_hw3.docx"
    source.write_text("pretend this is a docx")
    dest_dir = tmp_path / "anon"
    dest_dir.mkdir()

    result = anonymize_copy(str(source), str(dest_dir), "peer_submission.docx")

    assert not result.metadata_stripped
    assert result.warning is not None
    assert "not a PDF" not in result.warning  # exact wording check below
    assert "isn't a PDF" in result.warning
    assert result.path.endswith("peer_submission.docx")
    # The file was still copied under the anonymized name, content intact.
    assert open(result.path).read() == "pretend this is a docx"


def test_malformed_pdf_is_copied_but_flagged_not_crashed(tmp_path):
    source = tmp_path / "ada_lovelace_hw3.pdf"
    source.write_bytes(b"not actually a pdf")
    dest_dir = tmp_path / "anon"
    dest_dir.mkdir()

    result = anonymize_copy(str(source), str(dest_dir), "peer_submission.pdf")

    assert not result.metadata_stripped
    assert result.warning is not None
    assert result.path.endswith("peer_submission.pdf")


def test_strip_leading_pages_removes_a_gradescope_style_cover_page(tmp_path):
    source = tmp_path / "submission.pdf"
    make_multipage_pdf(source, [100, 200, 300])  # page 0 = "cover", 1 and 2 = real content
    dest_dir = tmp_path / "anon"
    dest_dir.mkdir()

    result = anonymize_copy(str(source), str(dest_dir), "peer_submission.pdf", strip_leading_pages=1)

    assert result.pages_stripped == 1
    assert result.warning is None

    from pypdf import PdfReader

    pages = PdfReader(result.path).pages
    assert len(pages) == 2
    assert float(pages[0].mediabox.width) == 200
    assert float(pages[1].mediabox.width) == 300


def test_strip_leading_pages_default_is_a_noop(tmp_path):
    source = tmp_path / "submission.pdf"
    make_multipage_pdf(source, [100, 200, 300])
    dest_dir = tmp_path / "anon"
    dest_dir.mkdir()

    result = anonymize_copy(str(source), str(dest_dir), "peer_submission.pdf")

    assert result.pages_stripped == 0
    from pypdf import PdfReader

    assert len(PdfReader(result.path).pages) == 3


def test_strip_leading_pages_refuses_to_empty_a_short_pdf(tmp_path):
    source = tmp_path / "submission.pdf"
    make_multipage_pdf(source, [100])  # only 1 page total
    dest_dir = tmp_path / "anon"
    dest_dir.mkdir()

    result = anonymize_copy(str(source), str(dest_dir), "peer_submission.pdf", strip_leading_pages=2)

    assert result.pages_stripped == 0
    assert result.warning is not None
    assert "only has 1" in result.warning

    from pypdf import PdfReader

    assert len(PdfReader(result.path).pages) == 1
