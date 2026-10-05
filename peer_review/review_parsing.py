"""Splitting a combined self-assessment + per-question peer-review submission.

Students submit ONE Canvas text-entry response covering two things: a
self-assessment of their own work, and their review of their assigned
peer's work -- and each of those two halves is graded against the same
rubric, one labeled block per question (see rubric.py). Only the
peer-review half should ever reach the peer -- the self-assessment is
between the student and the instructor.

The contract with students (spelled out in the send-packets comment,
generated from rubric.template_for, and worth also putting in the
assignment's own instructions) is:

    Self Assessment:
    Q1 - Correctness (out of 10):
    <score and justification>

    Q2 - Code Style (out of 5):
    <score and justification>

    Peer Review:
    Q1 - Correctness (out of 10):
    <score and justification>

    Q2 - Code Style (out of 5):
    <score and justification>

Order of the two top-level sections doesn't matter, and Canvas's rich
text editor wraps everything in HTML, so this strips tags and decodes
entities before hunting for any marker, matching all of them
case-insensitively with flexible whitespace.

If a submission doesn't contain a clean, unambiguous answer for every
rubric question -- a marker missing, duplicated, or the section itself
missing -- this deliberately refuses to guess and returns None with a
reason, rather than risk misattributing a score to the wrong question
or forwarding a garbled response. What comes back on success is
reconstructed using the rubric's own labels and ordering, not whatever
order or wording the student actually used, so the reviewee always
sees a consistently formatted response.
"""

from __future__ import annotations

import html
import re

from .rubric import RubricItem, format_question_header

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


def _split_top_level_sections(text: str) -> tuple[str | None, str | None, str | None]:
    """Returns (peer_review_text, self_assessment_text, error).

    Exactly one of the first two is non-None on success, or both are
    None and error explains why.
    """
    peer_matches = list(PEER_REVIEW_MARKER.finditer(text))
    if len(peer_matches) != 1:
        return None, None, (
            "missing a \"Peer Review:\" header" if not peer_matches else 'multiple "Peer Review:" headers found'
        )

    self_matches = list(SELF_ASSESSMENT_MARKER.finditer(text))
    if len(self_matches) > 1:
        return None, None, 'multiple "Self Assessment:" headers found'

    peer_start = peer_matches[0].end()
    peer_end = len(text)
    self_start = self_matches[0].end() if self_matches else None
    self_end = len(text)

    if self_matches:
        self_marker_start = self_matches[0].start()
        if self_marker_start > peer_start:
            peer_end = min(peer_end, self_marker_start)
        else:
            self_end = min(self_end, peer_matches[0].start())

    peer_text = text[peer_start:peer_end].strip()
    self_text = text[self_start:self_end].strip() if self_start is not None else None
    return peer_text, self_text, None


def _find_header_matches(section_text: str, item: RubricItem) -> list[re.Match]:
    """Finds where one rubric question's header appears in a section.

    Tries the exact header the packet template gave students first, with
    any run of whitespace (line breaks included) treated as equivalent --
    pasting a long header into Canvas's editor can wrap it across
    paragraphs, and that shouldn't cost a student their review. Matching
    the whole canonical header also means a label that happens to contain
    a colon can't truncate the header early and leave the rest of the
    label stuck to the front of the answer.

    Only if the canonical header isn't there does it fall back to the
    looser rule: the question id, then any run of non-colon text on the
    same line, up to a colon ("Q1:", or "Q1 - whatever:"). (?!\\w) stops
    "Q1" from matching inside "Q10".
    """
    canonical = r"\s+".join(re.escape(tok) for tok in format_question_header(item).split())
    matches = list(re.finditer(canonical, section_text, re.IGNORECASE))
    if matches:
        return matches
    loose = re.escape(item.question) + r"(?!\w)[^:\n]*:"
    return list(re.finditer(loose, section_text, re.IGNORECASE))


def _parse_questions(section_text: str, rubric_items: list[RubricItem]) -> tuple[dict[str, str] | None, str | None]:
    """Extracts {question_id: answer_text} from one section's plain text."""
    markers = {}
    for item in rubric_items:
        matches = _find_header_matches(section_text, item)
        if len(matches) != 1:
            return None, (
                f'question "{item.question}" is missing its header'
                if not matches
                else f'question "{item.question}" header appears more than once'
            )
        markers[item.question] = matches[0]

    ordered = sorted(markers.items(), key=lambda kv: kv[1].start())
    answers: dict[str, str] = {}
    for i, (question, match) in enumerate(ordered):
        start = match.end()
        end = ordered[i + 1][1].start() if i + 1 < len(ordered) else len(section_text)
        answer = section_text[start:end].strip()
        if not answer:
            return None, f'question "{question}" has no answer under its header'
        answers[question] = answer

    return answers, None


def parse_peer_review(
    body_html: str | None, rubric_items: list[RubricItem]
) -> tuple[str | None, str | None]:
    """Extracts and reconstructs the peer-review half of a submission.

    Returns (forward_text, error). forward_text is ready to post as-is
    to the reviewee: every rubric question, in rubric order, using the
    rubric's own header wording -- never the student's raw text for the
    headers, only for the answers. error is a human-readable reason
    when forward_text is None, meant for the instructor, not the
    student.
    """
    if not body_html:
        return None, "submission has no text body"

    text = _to_plain_text(body_html)
    peer_text, _self_text, error = _split_top_level_sections(text)
    if error:
        return None, error

    answers, error = _parse_questions(peer_text, rubric_items)
    if error:
        return None, f"in the Peer Review section, {error}"

    blocks = [f"{format_question_header(item)}\n{answers[item.question]}" for item in rubric_items]
    return "\n\n".join(blocks), None
