"""Best-effort anonymization for a file handed to a reviewer.

There are three distinct leak vectors, and they need different fixes:

1. The file's own name. Gradescope's bulk download typically names
   files after the student (e.g. "ada_lovelace_hw3.pdf"), and Canvas
   displays whatever filename an attachment was uploaded under. This
   is fully fixable: copy the file to a generic name before handing it
   to Canvas.

2. A known identifying page. Gradescope itself prepends an auto-
   generated cover/grading page to every exported PDF, printing the
   student's name and the rubric outline -- so page 1 of a Gradescope
   export is reliably identifying, not a guess about some unknown
   assignment template. strip_leading_pages removes it (and any
   further fixed number of leading pages, if your export has more).

3. Content embedded IN the file elsewhere -- a name typed into the
   student's own homework template, a LaTeX \\author{} tag, a
   "Creator"/"Author" field a word processor filled in automatically.
   This is NOT reliably fixable in general -- this tool has no idea
   what a given assignment's template looks like beyond the known
   Gradescope cover page. What it *can* do is strip the common PDF
   metadata fields (Author/Creator/Producer/Subject/Title), which
   catches a surprisingly common and easy-to-miss leak: plenty of
   students never notice their real name is sitting in a PDF's
   metadata even when every visible page is anonymous. It cannot touch
   arbitrary content on the remaining pages. That distinction is
   reported back rather than silently assumed away, so it ends up in
   front of the instructor.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path


@dataclass
class AnonymizedFile:
    path: str
    metadata_stripped: bool
    pages_stripped: int
    warning: str | None


def anonymize_copy(
    source_path: str, dest_dir: str, display_name: str, *, strip_leading_pages: int = 0
) -> AnonymizedFile:
    """Copy source_path into dest_dir under display_name, stripping PDF metadata
    (and, if requested, a fixed number of leading pages -- e.g. Gradescope's
    auto-generated cover page) if possible.

    Always returns a usable path (the plain copy, even if metadata/page
    stripping fails or doesn't apply) -- callers should still surface
    `warning` to a human rather than treat it as fully handled.
    """
    dest_path = str(Path(dest_dir) / display_name)
    shutil.copyfile(source_path, dest_path)

    if not display_name.lower().endswith(".pdf"):
        return AnonymizedFile(
            path=dest_path,
            metadata_stripped=False,
            pages_stripped=0,
            warning=(
                f"{display_name!r} isn't a PDF -- the filename was anonymized, but this "
                "tool only knows how to check PDF metadata/pages, so its content was not "
                "checked for identifying information"
            ),
        )

    try:
        from pypdf import PdfReader, PdfWriter
    except ImportError:
        return AnonymizedFile(
            path=dest_path,
            metadata_stripped=False,
            pages_stripped=0,
            warning=(
                "pypdf isn't installed -- the filename was anonymized, but PDF metadata "
                "(which often contains the real author's name) was not stripped, and no "
                "pages were removed. Install pypdf to fix this."
            ),
        )

    try:
        reader = PdfReader(dest_path)
        total_pages = len(reader.pages)

        pages_stripped = 0
        warning = None
        pages_to_keep = reader.pages
        if strip_leading_pages > 0:
            if strip_leading_pages >= total_pages:
                warning = (
                    f"asked to strip {strip_leading_pages} leading page(s) but the PDF only "
                    f"has {total_pages} -- left all pages in place to avoid emptying the "
                    "submission; check this file manually"
                )
            else:
                pages_to_keep = reader.pages[strip_leading_pages:]
                pages_stripped = strip_leading_pages

        writer = PdfWriter()
        for page in pages_to_keep:
            writer.add_page(page)
        writer.add_metadata({})  # a fresh PdfWriter never imported the source's /Info dict
        with open(dest_path, "wb") as f:
            writer.write(f)
        return AnonymizedFile(
            path=dest_path, metadata_stripped=True, pages_stripped=pages_stripped, warning=warning
        )
    except Exception as exc:  # noqa: BLE001 -- a malformed/encrypted PDF shouldn't kill the batch
        return AnonymizedFile(
            path=dest_path,
            metadata_stripped=False,
            pages_stripped=0,
            warning=f"Could not strip PDF metadata/pages ({exc}) -- check this file manually",
        )
