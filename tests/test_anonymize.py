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
