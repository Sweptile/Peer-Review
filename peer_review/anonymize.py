"""Best-effort anonymization for a file handed to a reviewer.

There are two distinct leak vectors, and they need different fixes:

1. The file's own name. Gradescope's bulk download typically names
   files after the student (e.g. "ada_lovelace_hw3.pdf"), and Canvas
   displays whatever filename an attachment was uploaded under. This
   is fully fixable: copy the file to a generic name before handing it
   to Canvas.

2. Content embedded IN the file -- a name typed into a homework
   template's header, a LaTeX \\author{} tag, a "Creator"/"Author"
   field a word processor filled in automatically when the student
   exported to PDF. This is NOT reliably fixable in general -- this
   tool has no idea what a given assignment's template looks like.
   What it *can* do is strip the common PDF metadata fields
   (Author/Creator/Producer/Subject/Title), which catches a
   surprisingly common and easy-to-miss leak: plenty of students never
   notice their real name is sitting in a PDF's metadata even when
   every visible page is anonymous. It cannot touch anything actually
   printed on the pages. That distinction is reported back rather than
   silently assumed away, so it ends up in front of the instructor.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path


@dataclass
class AnonymizedFile:
    path: str
    metadata_stripped: bool
    warning: str | None


def anonymize_copy(source_path: str, dest_dir: str, display_name: str) -> AnonymizedFile:
    """Copy source_path into dest_dir under display_name, stripping PDF metadata if possible.

    Always returns a usable path (the plain copy, even if metadata
    stripping fails or doesn't apply) -- callers should still surface
    `warning` to a human rather than treat it as fully handled.
    """
    dest_path = str(Path(dest_dir) / display_name)
    shutil.copyfile(source_path, dest_path)

    if not display_name.lower().endswith(".pdf"):
        return AnonymizedFile(
            path=dest_path,
            metadata_stripped=False,
            warning=(
                f"{display_name!r} isn't a PDF -- the filename was anonymized, but this "
                "tool only knows how to check PDF metadata, so its content and metadata "
                "were not checked for identifying information"
            ),
        )

    try:
        from pypdf import PdfReader, PdfWriter
    except ImportError:
        return AnonymizedFile(
            path=dest_path,
            metadata_stripped=False,
            warning=(
                "pypdf isn't installed -- the filename was anonymized, but PDF metadata "
                "(which often contains the real author's name) was not stripped. "
                "Install pypdf to fix this."
            ),
        )

    try:
        reader = PdfReader(dest_path)
        writer = PdfWriter()
        for page in reader.pages:
            writer.add_page(page)
        writer.add_metadata({})  # a fresh PdfWriter never imported the source's /Info dict
        with open(dest_path, "wb") as f:
            writer.write(f)
        return AnonymizedFile(path=dest_path, metadata_stripped=True, warning=None)
    except Exception as exc:  # noqa: BLE001 -- a malformed/encrypted PDF shouldn't kill the batch
        return AnonymizedFile(
            path=dest_path,
            metadata_stripped=False,
            warning=f"Could not strip PDF metadata ({exc}) -- check this file manually",
        )
