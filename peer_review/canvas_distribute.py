"""Move peer-review material through Canvas, without touching Gradescope.

Why this exists: your Canvas assignment is an External Tool launch
straight into Gradescope, so Canvas never holds the submitted file --
Canvas's native "Require Peer Reviews" feature has nothing to hand
out, and Gradescope has no student-facing peer-grading mode at all.
This module is the workaround, built around one real Canvas assignment
you create (e.g. "Peer Review") where each student submits ONE
text-entry response covering two things: a self-assessment of their
own work, and their review of their assigned peer's work.

Grading isn't one free-form paragraph -- it's per-question, against a
rubric (see rubric.py): each of the two halves (self-assessment, peer
review) is broken into one labeled block per rubric question, and
review_parsing.py parses those out individually rather than treating
either half as an opaque blob.

Both legs of the loop post *private submission comments* on that same
"Peer Review" assignment -- visible only to the student and you, right
on the page they already go to submit, no separate inbox to check:

  1. send-packets    -- before a reviewer has submitted anything, they
     get a comment on their own (still-empty) "Peer Review" submission
     with their own file and their assigned peer's file attached, plus
     the exact per-question template (generated from the rubric) they
     need to fill in under "Self Assessment:" and "Peer Review:".

  2. forward-reviews -- once reviewers have submitted their combined
     write-ups, each *reviewee* gets a comment on THEIR OWN "Peer
     Review" submission containing just the per-question answers from
     the "Peer Review:" section their reviewer wrote about them,
     reconstructed using the rubric's own headers (the self-assessment
     half is never forwarded). (Every student is both a reviewer and a
     reviewee under the matching this project generates, so they
     already have their own submission there to comment on.)

Grading the review itself needs no extra tooling from this project at
all: once reviews are real submissions on a real Canvas assignment,
grade that assignment in SpeedGrader exactly like any other -- that's
the point of routing reviews through a Canvas assignment instead of,
say, a spreadsheet.

Both commands default to double-blind: send_packets never names the
peer whose work is attached (and hands over an anonymized copy of
their file -- see anonymize.py), and forward_reviews never names the
reviewer when it comments their feedback onto the reviewee. Nothing
here can scrub a name a student types into their own review text or
bakes into a non-PDF file's content -- that's on the assignment
instructions, not this tool. Pass anonymous=False for the old
identities-visible behavior.

Nothing is sent to Canvas until you pass --live on the CLI. Without
it, both commands run in dry-run mode: they resolve every student
against the Canvas roster and print exactly what would be posted.
"""

from __future__ import annotations

import csv
import tempfile
from dataclasses import dataclass
from pathlib import Path

from canvasapi import Canvas

from .anonymize import anonymize_copy
from .review_parsing import parse_peer_review
from .rubric import RubricItem, template_for


@dataclass
class ActionResult:
    email: str
    ok: bool
    detail: str


def _load_assignments_csv(path: str) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        raise ValueError(f"{path} has no rows")
    required = {
        "reviewer_name",
        "reviewer_email",
        "reviewer_submission_file",
        "peer_name",
        "peer_email",
        "peer_submission_file",
    }
    missing = required - set(rows[0].keys())
    if missing:
        raise ValueError(f"{path} is missing column(s): {sorted(missing)}")
    return rows


def _roster_by_email(course) -> dict:
    roster = {}
    for user in course.get_users(enrollment_type=["student"], include=["email"]):
        email = getattr(user, "email", None)
        if email:
            roster[email.strip().lower()] = user
    return roster


def _post_comment(submission, *, text: str | None, attachment_paths: list[str]) -> None:
    if text:
        submission.edit(comment={"text_comment": text, "group_comment": False})
    for path in attachment_paths:
        submission.upload_comment(path)


def send_packets(
    *,
    canvas_url: str,
    token: str,
    course_id: int,
    peer_review_assignment_id: int,
    assignments_csv: str,
    rubric_items: list[RubricItem],
    live: bool = False,
    anonymous: bool = True,
    strip_leading_pages: int = 0,
) -> list[ActionResult]:
    """Comment on each reviewer's own file + their assigned peer's file."""
    rows = _load_assignments_csv(assignments_csv)
    canvas = Canvas(canvas_url, token)
    course = canvas.get_course(course_id)
    assignment = course.get_assignment(peer_review_assignment_id)
    roster = _roster_by_email(course)
    rubric_template = template_for(rubric_items)

    results: list[ActionResult] = []
    with tempfile.TemporaryDirectory() as tmp_dir:
        for row in rows:
            email = row["reviewer_email"].strip().lower()
            user = roster.get(email)
            if user is None:
                results.append(ActionResult(email, False, "No matching Canvas enrollment for this email"))
                continue

            peer_file = row["peer_submission_file"]
            anon_warning = None
            if anonymous:
                display_name = "peer_submission" + Path(peer_file).suffix
                anon = anonymize_copy(
                    peer_file, tmp_dir, display_name, strip_leading_pages=strip_leading_pages
                )
                peer_file = anon.path
                anon_warning = anon.warning

            if anonymous:
                note = (
                    "You're assigned to grade your own submission (attached) and an "
                    "anonymous peer's submission (also attached), against the rubric below. "
                    "Submit ONE response to this assignment with two sections, using exactly "
                    "these headers on their own line, and answer every question under both:\n\n"
                    f"Self Assessment:\n{rubric_template}\n\n"
                    f"Peer Review:\n{rubric_template}\n\n"
                    "Only the text under \"Peer Review:\" will be shared with your peer -- "
                    "keep your self-assessment separate from it, and don't put your name "
                    "anywhere in your review (your peer won't be told who wrote it)."
                )
            else:
                note = (
                    f"You're assigned to grade your own submission (attached) and "
                    f"{row['peer_name']}'s submission (also attached), against the rubric below. "
                    f"Submit ONE response to this assignment with two sections, using exactly "
                    f"these headers on their own line, and answer every question under both:\n\n"
                    f"Self Assessment:\n{rubric_template}\n\n"
                    f"Peer Review:\n{rubric_template}\n\n"
                    f"Only the text under \"Peer Review:\" will be shared with {row['peer_name']} -- "
                    f"keep your self-assessment separate from it."
                )
            attachments = [row["reviewer_submission_file"], peer_file]

            if not live:
                detail = (
                    f"[DRY RUN] would comment on Canvas user {user.id} ({user.name})'s "
                    f'"Peer Review" submission: "{note}" + attach {attachments}'
                )
                if anon_warning:
                    detail += f" -- WARNING: {anon_warning}"
                results.append(ActionResult(email, True, detail))
                continue

            try:
                submission = assignment.get_submission(user.id)
                _post_comment(submission, text=note, attachment_paths=attachments)
                detail = "posted" if not anon_warning else f"posted -- WARNING: {anon_warning}"
                results.append(ActionResult(email, True, detail))
            except Exception as exc:  # noqa: BLE001 -- surfaced per-student, not fatal to the batch
                results.append(ActionResult(email, False, f"Canvas API error: {exc}"))

    return results


def forward_reviews(
    *,
    canvas_url: str,
    token: str,
    course_id: int,
    peer_review_assignment_id: int,
    assignments_csv: str,
    rubric_items: list[RubricItem],
    live: bool = False,
    anonymous: bool = True,
) -> list[ActionResult]:
    """Pull each reviewer's submitted write-up and comment it onto the reviewee's own submission."""
    rows = _load_assignments_csv(assignments_csv)
    canvas = Canvas(canvas_url, token)
    course = canvas.get_course(course_id)
    assignment = course.get_assignment(peer_review_assignment_id)
    roster = _roster_by_email(course)

    results: list[ActionResult] = []
    for row in rows:
        reviewer_email = row["reviewer_email"].strip().lower()
        reviewee_email = row["peer_email"].strip().lower()
        reviewer = roster.get(reviewer_email)
        reviewee = roster.get(reviewee_email)

        if reviewer is None:
            results.append(ActionResult(reviewer_email, False, "Reviewer has no matching Canvas enrollment"))
            continue
        if reviewee is None:
            results.append(ActionResult(reviewee_email, False, "Reviewee has no matching Canvas enrollment"))
            continue

        reviewer_submission = assignment.get_submission(reviewer.id)
        if getattr(reviewer_submission, "workflow_state", "unsubmitted") == "unsubmitted":
            results.append(
                ActionResult(reviewee_email, False, f"{row['reviewer_name']} hasn't submitted a review yet -- skipped")
            )
            continue

        review_text, parse_error = parse_peer_review(getattr(reviewer_submission, "body", None), rubric_items)
        if review_text is None:
            results.append(
                ActionResult(
                    reviewee_email,
                    False,
                    f"Couldn't parse {row['reviewer_name']}'s peer review ({parse_error}) -- "
                    "check it and forward manually",
                )
            )
            continue

        if anonymous:
            note = f"Feedback on your submission from an anonymous peer reviewer:\n\n{review_text}"
        else:
            note = f"Feedback on your submission from {row['reviewer_name']}:\n\n{review_text}"

        if not live:
            results.append(
                ActionResult(
                    reviewee_email,
                    True,
                    f"[DRY RUN] would comment on Canvas user {reviewee.id} ({reviewee.name})'s "
                    f"own \"Peer Review\" submission with {row['reviewer_name']}'s review "
                    f"({len(review_text)} chars)",
                )
            )
            continue

        try:
            reviewee_submission = assignment.get_submission(reviewee.id)
            _post_comment(reviewee_submission, text=note, attachment_paths=[])
            results.append(ActionResult(reviewee_email, True, "posted"))
        except Exception as exc:  # noqa: BLE001
            results.append(ActionResult(reviewee_email, False, f"Canvas API error: {exc}"))

    return results
