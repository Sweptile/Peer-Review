"""Tests for the Canvas delivery logic, using a fake Canvas client.

These don't hit the network -- they fake out just enough of the
canvasapi surface (get_course, get_users, get_assignment,
get_submission, submission.edit, submission.upload_comment) to prove
the *logic* is right: roster-email matching, dry-run vs --live gating,
and the unsubmitted/no-text-body skip paths. Whether the real
canvasapi calls are wired up correctly can only be confirmed against a
live Canvas instance, which this repo intentionally never touches on
its own.
"""

from __future__ import annotations

import csv
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from peer_review import canvas_distribute as cd


def make_user(uid, name, email):
    return SimpleNamespace(id=uid, name=name, email=email)


ROSTER = [
    (1, "Ada Lovelace", "ada@school.edu"),
    (2, "Alan Turing", "alan@school.edu"),
    (3, "Grace Hopper", "grace@school.edu"),
]


def make_fake_canvas(students, submissions=None):
    """students: list of (id, name, email).

    submissions: {user_id: SimpleNamespace(workflow_state=..., body=...)}
    used for reads (forward_reviews looking at the reviewer's write-up).
    Each returned submission is a MagicMock so .edit/.upload_comment
    calls can be asserted on regardless of read/write path.
    """
    users = [make_user(*s) for s in students]

    course = MagicMock()
    course.get_users.return_value = users

    assignment = MagicMock()
    submission_mocks = {}

    def get_submission(user_id, **kwargs):
        if user_id not in submission_mocks:
            base = (submissions or {}).get(user_id, SimpleNamespace(workflow_state="unsubmitted", body=None))
            m = MagicMock()
            m.workflow_state = base.workflow_state
            m.body = base.body
            submission_mocks[user_id] = m
        return submission_mocks[user_id]

    assignment.get_submission.side_effect = get_submission
    course.get_assignment.return_value = assignment

    canvas = MagicMock()
    canvas.get_course.return_value = course

    return canvas, course, assignment, submission_mocks


def write_assignments_csv(tmp_path, rows):
    path = tmp_path / "assignments.csv"
    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "reviewer_name",
                "reviewer_email",
                "reviewer_submission_file",
                "peer_name",
                "peer_email",
                "peer_submission_file",
            ]
        )
        writer.writerows(rows)
    return str(path)


@pytest.fixture(autouse=True)
def patch_canvas(monkeypatch):
    holder = {}

    def fake_canvas_ctor(url, token):
        return holder["canvas"]

    monkeypatch.setattr(cd, "Canvas", fake_canvas_ctor)
    return holder


def test_send_packets_dry_run_does_not_touch_submission(tmp_path, patch_canvas):
    canvas, course, assignment, subs = make_fake_canvas(ROSTER)
    patch_canvas["canvas"] = canvas

    assignments_csv = write_assignments_csv(
        tmp_path,
        [["Ada Lovelace", "ada@school.edu", "ada.pdf", "Alan Turing", "alan@school.edu", "alan.pdf"]],
    )

    results = cd.send_packets(
        canvas_url="https://fake",
        token="t",
        course_id=1,
        peer_review_assignment_id=99,
        assignments_csv=assignments_csv,
        live=False,
    )

    assert len(results) == 1
    assert results[0].ok
    assert "DRY RUN" in results[0].detail
    assignment.get_submission.assert_not_called()


def test_send_packets_live_comments_on_reviewers_own_submission(tmp_path, patch_canvas):
    canvas, course, assignment, subs = make_fake_canvas(ROSTER)
    patch_canvas["canvas"] = canvas

    reviewer_file = str(tmp_path / "ada.pdf")
    peer_file = str(tmp_path / "alan.pdf")
    (tmp_path / "ada.pdf").write_text("x")
    (tmp_path / "alan.pdf").write_text("x")

    assignments_csv = write_assignments_csv(
        tmp_path,
        [["Ada Lovelace", "ada@school.edu", reviewer_file, "Alan Turing", "alan@school.edu", peer_file]],
    )

    results = cd.send_packets(
        canvas_url="https://fake",
        token="t",
        course_id=1,
        peer_review_assignment_id=99,
        assignments_csv=assignments_csv,
        live=True,
    )

    assert results[0].ok
    assignment.get_submission.assert_called_once_with(1)  # Ada's own submission, not Alan's
    ada_submission = subs[1]
    ada_submission.edit.assert_called_once()
    _, kwargs = ada_submission.edit.call_args
    assert "Alan Turing" in kwargs["comment"]["text_comment"]
    assert ada_submission.upload_comment.call_count == 2
    ada_submission.upload_comment.assert_any_call(reviewer_file)
    ada_submission.upload_comment.assert_any_call(peer_file)


def test_send_packets_reports_unmatched_email(tmp_path, patch_canvas):
    canvas, course, assignment, subs = make_fake_canvas(ROSTER)
    patch_canvas["canvas"] = canvas

    assignments_csv = write_assignments_csv(
        tmp_path,
        [["Ghost Student", "ghost@school.edu", "g.pdf", "Alan Turing", "alan@school.edu", "alan.pdf"]],
    )

    results = cd.send_packets(
        canvas_url="https://fake",
        token="t",
        course_id=1,
        peer_review_assignment_id=99,
        assignments_csv=assignments_csv,
        live=False,
    )

    assert not results[0].ok
    assert "No matching Canvas enrollment" in results[0].detail


def test_forward_reviews_skips_unsubmitted(tmp_path, patch_canvas):
    canvas, course, assignment, subs = make_fake_canvas(ROSTER, submissions={})
    patch_canvas["canvas"] = canvas

    assignments_csv = write_assignments_csv(
        tmp_path,
        [["Ada Lovelace", "ada@school.edu", "ada.pdf", "Alan Turing", "alan@school.edu", "alan.pdf"]],
    )

    results = cd.forward_reviews(
        canvas_url="https://fake",
        token="t",
        course_id=1,
        peer_review_assignment_id=99,
        assignments_csv=assignments_csv,
        live=False,
    )

    assert not results[0].ok
    assert "hasn't submitted" in results[0].detail


def test_forward_reviews_skips_no_text_body(tmp_path, patch_canvas):
    submissions = {1: SimpleNamespace(workflow_state="submitted", body=None)}
    canvas, course, assignment, subs = make_fake_canvas(ROSTER, submissions=submissions)
    patch_canvas["canvas"] = canvas

    assignments_csv = write_assignments_csv(
        tmp_path,
        [["Ada Lovelace", "ada@school.edu", "ada.pdf", "Alan Turing", "alan@school.edu", "alan.pdf"]],
    )

    results = cd.forward_reviews(
        canvas_url="https://fake",
        token="t",
        course_id=1,
        peer_review_assignment_id=99,
        assignments_csv=assignments_csv,
        live=False,
    )

    assert not results[0].ok
    assert "no text body" in results[0].detail


def test_forward_reviews_dry_run_reports_would_post(tmp_path, patch_canvas):
    submissions = {1: SimpleNamespace(workflow_state="submitted", body="Great work, nice tests!")}
    canvas, course, assignment, subs = make_fake_canvas(ROSTER, submissions=submissions)
    patch_canvas["canvas"] = canvas

    assignments_csv = write_assignments_csv(
        tmp_path,
        [["Ada Lovelace", "ada@school.edu", "ada.pdf", "Alan Turing", "alan@school.edu", "alan.pdf"]],
    )

    results = cd.forward_reviews(
        canvas_url="https://fake",
        token="t",
        course_id=1,
        peer_review_assignment_id=99,
        assignments_csv=assignments_csv,
        live=False,
    )

    assert results[0].ok
    assert "DRY RUN" in results[0].detail
    # Only the read of the reviewer's own submission happened; nothing written.
    subs[1].edit.assert_not_called()


def test_forward_reviews_live_comments_reviewers_text_onto_reviewees_own_submission(tmp_path, patch_canvas):
    submissions = {1: SimpleNamespace(workflow_state="submitted", body="Great work, nice tests!")}
    canvas, course, assignment, subs = make_fake_canvas(ROSTER, submissions=submissions)
    patch_canvas["canvas"] = canvas

    assignments_csv = write_assignments_csv(
        tmp_path,
        [["Ada Lovelace", "ada@school.edu", "ada.pdf", "Alan Turing", "alan@school.edu", "alan.pdf"]],
    )

    results = cd.forward_reviews(
        canvas_url="https://fake",
        token="t",
        course_id=1,
        peer_review_assignment_id=99,
        assignments_csv=assignments_csv,
        live=True,
    )

    assert results[0].ok
    # Comment lands on Alan's (the reviewee's, user id 2) own submission.
    alan_submission = subs[2]
    alan_submission.edit.assert_called_once()
    _, kwargs = alan_submission.edit.call_args
    assert "Ada Lovelace" in kwargs["comment"]["text_comment"]
    assert "Great work, nice tests!" in kwargs["comment"]["text_comment"]
