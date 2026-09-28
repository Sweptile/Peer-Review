"""Tests for the Canvas delivery logic, using a fake Canvas client.

These don't hit the network -- they fake out just enough of the
canvasapi surface (get_course, get_users, get_assignment,
get_submission, create_conversation, get_current_user().upload) to
prove the *logic* is right: roster-email matching, dry-run vs --live
gating, and the unsubmitted/no-text-body skip paths. Whether the real
canvasapi calls are wired up correctly can only be confirmed against a
live Canvas instance, which this repo intentionally never touches on
its own.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from peer_review import canvas_distribute as cd


def make_user(uid, name, email):
    return SimpleNamespace(id=uid, name=name, email=email)


def make_fake_canvas(students, submissions=None):
    """students: list of (id, name, email). submissions: {user_id: SimpleNamespace}"""
    users = [make_user(*s) for s in students]

    course = MagicMock()
    course.get_users.return_value = users

    assignment = MagicMock()

    def get_submission(user_id, **kwargs):
        if submissions and user_id in submissions:
            return submissions[user_id]
        return SimpleNamespace(workflow_state="unsubmitted", body=None)

    assignment.get_submission.side_effect = get_submission
    course.get_assignment.return_value = assignment

    canvas = MagicMock()
    canvas.get_course.return_value = course
    current_user = MagicMock()
    current_user.upload.return_value = (True, {"id": 999})
    canvas.get_current_user.return_value = current_user

    return canvas, course, assignment


ROSTER = [
    (1, "Ada Lovelace", "ada@school.edu"),
    (2, "Alan Turing", "alan@school.edu"),
    (3, "Grace Hopper", "grace@school.edu"),
]


def write_assignments_csv(tmp_path, rows):
    import csv

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


def test_send_packets_dry_run_does_not_call_create_conversation(tmp_path, patch_canvas):
    canvas, course, _ = make_fake_canvas(ROSTER)
    patch_canvas["canvas"] = canvas

    assignments_csv = write_assignments_csv(
        tmp_path,
        [["Ada Lovelace", "ada@school.edu", "ada.pdf", "Alan Turing", "alan@school.edu", "alan.pdf"]],
    )

    results = cd.send_packets(
        canvas_url="https://fake",
        token="t",
        course_id=1,
        peer_review_assignment_name="Peer Review",
        assignments_csv=assignments_csv,
        live=False,
    )

    assert len(results) == 1
    assert results[0].ok
    assert "DRY RUN" in results[0].detail
    canvas.create_conversation.assert_not_called()


def test_send_packets_live_calls_create_conversation(tmp_path, patch_canvas):
    canvas, course, _ = make_fake_canvas(ROSTER)
    patch_canvas["canvas"] = canvas

    assignments_csv = write_assignments_csv(
        tmp_path,
        [["Ada Lovelace", "ada@school.edu", str(tmp_path / "ada.pdf"), "Alan Turing", "alan@school.edu", str(tmp_path / "alan.pdf")]],
    )
    (tmp_path / "ada.pdf").write_text("x")
    (tmp_path / "alan.pdf").write_text("x")

    results = cd.send_packets(
        canvas_url="https://fake",
        token="t",
        course_id=1,
        peer_review_assignment_name="Peer Review",
        assignments_csv=assignments_csv,
        live=True,
    )

    assert results[0].ok
    canvas.create_conversation.assert_called_once()
    _, kwargs = canvas.create_conversation.call_args
    assert kwargs["recipients"] == ["1"]  # Ada's Canvas user id
    assert kwargs["attachment_ids"] == [999, 999]


def test_send_packets_reports_unmatched_email(tmp_path, patch_canvas):
    canvas, course, _ = make_fake_canvas(ROSTER)
    patch_canvas["canvas"] = canvas

    assignments_csv = write_assignments_csv(
        tmp_path,
        [["Ghost Student", "ghost@school.edu", "g.pdf", "Alan Turing", "alan@school.edu", "alan.pdf"]],
    )

    results = cd.send_packets(
        canvas_url="https://fake",
        token="t",
        course_id=1,
        peer_review_assignment_name="Peer Review",
        assignments_csv=assignments_csv,
        live=False,
    )

    assert not results[0].ok
    assert "No matching Canvas enrollment" in results[0].detail


def test_forward_reviews_skips_unsubmitted(tmp_path, patch_canvas):
    canvas, course, assignment = make_fake_canvas(ROSTER, submissions={})
    patch_canvas["canvas"] = canvas

    assignments_csv = write_assignments_csv(
        tmp_path,
        [["Ada Lovelace", "ada@school.edu", "ada.pdf", "Alan Turing", "alan@school.edu", "alan.pdf"]],
    )

    results = cd.forward_reviews(
        canvas_url="https://fake",
        token="t",
        course_id=1,
        peer_review_assignment_id=42,
        assignments_csv=assignments_csv,
        live=False,
    )

    assert not results[0].ok
    assert "hasn't submitted" in results[0].detail


def test_forward_reviews_skips_no_text_body(tmp_path, patch_canvas):
    submissions = {1: SimpleNamespace(workflow_state="submitted", body=None)}
    canvas, course, assignment = make_fake_canvas(ROSTER, submissions=submissions)
    patch_canvas["canvas"] = canvas

    assignments_csv = write_assignments_csv(
        tmp_path,
        [["Ada Lovelace", "ada@school.edu", "ada.pdf", "Alan Turing", "alan@school.edu", "alan.pdf"]],
    )

    results = cd.forward_reviews(
        canvas_url="https://fake",
        token="t",
        course_id=1,
        peer_review_assignment_id=42,
        assignments_csv=assignments_csv,
        live=False,
    )

    assert not results[0].ok
    assert "no text body" in results[0].detail


def test_forward_reviews_dry_run_reports_would_send(tmp_path, patch_canvas):
    submissions = {1: SimpleNamespace(workflow_state="submitted", body="Great work, nice tests!")}
    canvas, course, assignment = make_fake_canvas(ROSTER, submissions=submissions)
    patch_canvas["canvas"] = canvas

    assignments_csv = write_assignments_csv(
        tmp_path,
        [["Ada Lovelace", "ada@school.edu", "ada.pdf", "Alan Turing", "alan@school.edu", "alan.pdf"]],
    )

    results = cd.forward_reviews(
        canvas_url="https://fake",
        token="t",
        course_id=1,
        peer_review_assignment_id=42,
        assignments_csv=assignments_csv,
        live=False,
    )

    assert results[0].ok
    assert "DRY RUN" in results[0].detail
    canvas.create_conversation.assert_not_called()


def test_forward_reviews_live_sends_reviewers_text_to_reviewee(tmp_path, patch_canvas):
    submissions = {1: SimpleNamespace(workflow_state="submitted", body="Great work, nice tests!")}
    canvas, course, assignment = make_fake_canvas(ROSTER, submissions=submissions)
    patch_canvas["canvas"] = canvas

    assignments_csv = write_assignments_csv(
        tmp_path,
        [["Ada Lovelace", "ada@school.edu", "ada.pdf", "Alan Turing", "alan@school.edu", "alan.pdf"]],
    )

    results = cd.forward_reviews(
        canvas_url="https://fake",
        token="t",
        course_id=1,
        peer_review_assignment_id=42,
        assignments_csv=assignments_csv,
        live=True,
    )

    assert results[0].ok
    canvas.create_conversation.assert_called_once()
    _, kwargs = canvas.create_conversation.call_args
    assert kwargs["recipients"] == ["2"]  # Alan (the reviewee) gets Ada's review
    assert kwargs["body"] == "Great work, nice tests!"
