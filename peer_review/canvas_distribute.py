"""Move peer-review material through Canvas, without touching Gradescope.

Why this exists: your Canvas assignment is an External Tool launch
straight into Gradescope, so Canvas never holds the submitted file --
Canvas's native "Require Peer Reviews" feature has nothing to hand
out, and Gradescope has no student-facing peer-grading mode. This
module is the workaround, built around a real Canvas assignment you
create (e.g. "Peer Review") where students type/upload their review
of their assigned peer's work -- because that's what makes step 2
below possible using nothing but Canvas's own gradebook.

The loop has two legs, each a separate CLI command so you can run them
weeks apart:

  1. send-packets   -- after you've downloaded submissions from
     Gradescope yourself, each *reviewer* gets a private Canvas inbox
     message with their own file + their assigned peer's file
     attached, telling them which peer to review and pointing them at
     the "Peer Review" assignment to submit their write-up to.

  2. forward-reviews -- once reviewers have submitted their write-ups
     to that assignment, each *reviewee* gets a private Canvas inbox
     message containing the text their reviewer wrote about their
     work.

Grading the review itself (your "different assignment" score) needs
no extra tooling from this project at all: once step 2's reviews are
real submissions on a real Canvas assignment, you grade that
assignment in SpeedGrader exactly like any other -- that's the whole
point of routing reviews through a Canvas assignment instead of, say,
a spreadsheet.

Delivery uses Canvas Conversations (the inbox), not submission
comments, so it doesn't depend on a submission object already
existing and doesn't care which assignment page (if any) it's near.

Nothing is sent to Canvas until you pass --live on the CLI. Without
it, both commands run in dry-run mode: they resolve every student
against the Canvas roster and print exactly what would be sent.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass

from canvasapi import Canvas


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


def _send_message(canvas: Canvas, *, user_id: int, subject: str, body: str, attachment_paths: list[str]) -> None:
    attachment_ids = []
    me = canvas.get_current_user()
    for path in attachment_paths:
        ok, response = me.upload(path)
        if not ok:
            raise RuntimeError(f"Upload failed for {path}: {response}")
        attachment_ids.append(response["id"])

    canvas.create_conversation(
        recipients=[str(user_id)],
        body=body,
        subject=subject,
        attachment_ids=attachment_ids,
    )


def send_packets(
    *,
    canvas_url: str,
    token: str,
    course_id: int,
    peer_review_assignment_name: str,
    assignments_csv: str,
    live: bool = False,
) -> list[ActionResult]:
    """Send each reviewer their own file + their assigned peer's file."""
    rows = _load_assignments_csv(assignments_csv)
    canvas = Canvas(canvas_url, token)
    course = canvas.get_course(course_id)
    roster = _roster_by_email(course)

    results: list[ActionResult] = []
    for row in rows:
        email = row["reviewer_email"].strip().lower()
        user = roster.get(email)
        if user is None:
            results.append(ActionResult(email, False, "No matching Canvas enrollment for this email"))
            continue

        subject = "Your peer review assignment"
        body = (
            f"You're assigned to review {row['peer_name']}'s submission (attached), "
            f"alongside your own submission (also attached) for comparison. "
            f"Please submit your written review to the \"{peer_review_assignment_name}\" "
            f"assignment on Canvas."
        )
        attachments = [row["reviewer_submission_file"], row["peer_submission_file"]]

        if not live:
            results.append(
                ActionResult(
                    email,
                    True,
                    f"[DRY RUN] would message Canvas user {user.id} ({user.name}): "
                    f'"{body}" + attach {attachments}',
                )
            )
            continue

        try:
            _send_message(canvas, user_id=user.id, subject=subject, body=body, attachment_paths=attachments)
            results.append(ActionResult(email, True, "sent"))
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
    live: bool = False,
) -> list[ActionResult]:
    """Pull each reviewer's submitted write-up and send it to the reviewee."""
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

        submission = assignment.get_submission(reviewer.id)
        if getattr(submission, "workflow_state", "unsubmitted") == "unsubmitted":
            results.append(
                ActionResult(reviewee_email, False, f"{row['reviewer_name']} hasn't submitted a review yet -- skipped")
            )
            continue

        review_text = getattr(submission, "body", None)
        if not review_text:
            results.append(
                ActionResult(
                    reviewee_email,
                    False,
                    f"{row['reviewer_name']}'s submission has no text body (likely a file upload) -- "
                    "forward it to the reviewee manually",
                )
            )
            continue

        subject = f"Peer feedback on your submission from {row['reviewer_name']}"
        body = review_text

        if not live:
            results.append(
                ActionResult(
                    reviewee_email,
                    True,
                    f"[DRY RUN] would message Canvas user {reviewee.id} ({reviewee.name}) "
                    f"with {row['reviewer_name']}'s review ({len(review_text)} chars)",
                )
            )
            continue

        try:
            _send_message(canvas, user_id=reviewee.id, subject=subject, body=body, attachment_paths=[])
            results.append(ActionResult(reviewee_email, True, "sent"))
        except Exception as exc:  # noqa: BLE001
            results.append(ActionResult(reviewee_email, False, f"Canvas API error: {exc}"))

    return results
