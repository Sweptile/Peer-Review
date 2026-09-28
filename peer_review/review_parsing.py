"""Splitting a combined self-assessment + peer-review submission.

Students submit ONE Canvas text-entry response covering two things: a
self-assessment of their own work, and their review of their assigned
peer's work. Only the peer-review half should ever reach the peer --
the self-assessment is between the student and the instructor. So
forward_reviews can't just forward the whole submission body; it has
to reliably pull out just the peer-review section.

The contract with students (this should be spelled out both in the
assignment instructions and in the send-packets comment) is two
literal section headers, each starting its own line, order-agnostic:

    Self Assessment:
    <...>

    Peer Review:
    <...>

Canvas's rich text editor wraps submissions in HTML, so this strips
tags and decodes entities before hunting for the markers, and matches
them case-insensitively with flexible whitespace.

If the markers can't be found unambiguously -- missing, or written
more than once -- this deliberately returns None rather than guessing.
Forwarding a garbled section, or worse, a piece of someone's private
self-assessment, to the wrong person is a much worse failure than
making the instructor forward one submission by hand.
"""

from __future__ import annotations

import html
import re

_TAG_RE = re.compile(r"<[^>]+>")
_INLINE_WS_RE = re.compile(r"[ \t]+")
_BLANK_RUN_RE = re.compile(r"\n{3,}")

SELF_ASSESSMENT_MARKER = re.compile(r"self\s*assessment\s*:", re.IGNORECASE)
PEER_REVIEW_MARKER = re.compile(r"peer\s*review\s*:", re.IGNORECASE)


def _to_plain_text(body_html: str) -> str:
    text = _TAG_RE.sub("\n", body_html)
    text = html.unescape(text)
    lines = [_INLINE_WS_RE.sub(" ", line).strip() for line in text.splitlines()]
    text = "\n".join(lines)
    text = _BLANK_RUN_RE.sub("\n\n", text)
    return text.strip()


def extract_peer_review_section(body_html: str | None) -> str | None:
    """Returns the text under the "Peer Review:" header, or None if the
    submission doesn't contain exactly one unambiguous marker for it."""
    if not body_html:
        return None

    text = _to_plain_text(body_html)

    peer_matches = list(PEER_REVIEW_MARKER.finditer(text))
    if len(peer_matches) != 1:
        return None  # missing, or the student used the header more than once -- ambiguous

    self_matches = list(SELF_ASSESSMENT_MARKER.finditer(text))
    if len(self_matches) > 1:
        return None  # ambiguous for the same reason

    start = peer_matches[0].end()
    end = len(text)
    for m in self_matches:
        # A self-assessment header that comes *after* the peer-review
        # header bounds where the peer-review section ends. One that
        # comes before it doesn't affect the end, since the slice
        # already starts after the peer-review header.
        if m.start() > start:
            end = min(end, m.start())

    section = text[start:end].strip()
    return section or None
