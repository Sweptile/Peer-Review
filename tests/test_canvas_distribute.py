"""Tests for the Canvas delivery logic, using a fake Canvas client.

These don't hit the network -- they fake out just enough of the
canvasapi surface (get_course, get_users, get_assignment,
get_submission, submission.edit, submission.upload_comment) to prove
the *logic* is right: roster-email matching, dry-run vs --live gating,
and the unsubmitted/unparseable-submission skip paths. Whether the
real canvasapi calls are wired up correctly can only be confirmed
against a live Canvas instance, which this repo intentionally never
touches on its own.
"""

from __future__ import annotations

import csv
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from peer_review import canvas_distribute as cd
from peer_review.rubric import RubricItem

RUBRIC = [
    RubricItem(question="Q1", label="Correctness", max_points="10"),
    RubricItem(question="Q2", label="Code Style", max_points="5"),
]


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


@pytest.fixture(autouse=True)
def patch_upload_attachment(monkeypatch):
    """_upload_attachment talks to canvasapi's low-level Uploader, which
    needs a real HTTP round trip -- not something to fake through several
    layers of MagicMock. Patch it directly and record (submission, path)
    calls so tests can assert on what would have been uploaded."""
    calls = []

    def fake_upload(submission, path):
        calls.append((submission, path))
        return len(calls)  # a fake but distinct file id per call

    monkeypatch.setattr(cd, "_upload_attachment", fake_upload)
    return calls


def test_send_packets_dry_run_does_not_touch_submission(tmp_path, patch_canvas):
    canvas, course, assignment, subs = make_fake_canvas(ROSTER)
    patch_canvas["canvas"] = canvas

    # Dry run still needs the real files on disk: it actually performs the
    # anonymization step (not just the Canvas comment) so problems like a
    # missing pypdf install or a malformed PDF surface before --live.
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
        rubric_items=RUBRIC,
        live=False,
    )

    assert len(results) == 1
    assert results[0].ok
    assert "DRY RUN" in results[0].detail
    # The rubric template for both sections must be in what's shown.
    assert "Q1 - Correctness (out of 10):" in results[0].detail
    assert "Q2 - Code Style (out of 5):" in results[0].detail
    assignment.get_submission.assert_not_called()


def test_send_packets_live_comments_on_reviewers_own_submission_identities_visible(
    tmp_path, patch_canvas, patch_upload_attachment
):
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
        rubric_items=RUBRIC,
        live=True,
        anonymous=False,
    )

    assert results[0].ok
    assignment.get_submission.assert_called_once_with(1)  # Ada's own submission, not Alan's
    ada_submission = subs[1]
    # Text and attachments must land in ONE edit call, not one call per piece --
    # a partial failure mid-upload should never leave a text-only comment behind.
    ada_submission.edit.assert_called_once()
    _, kwargs = ada_submission.edit.call_args
    text = kwargs["comment"]["text_comment"]
    assert "Alan Turing" in text
    assert "Q1 - Correctness (out of 10):" in text
    assert "Q2 - Code Style (out of 5):" in text
    assert len(kwargs["comment"]["file_ids"]) == 2

    uploaded_paths = [path for _sub, path in patch_upload_attachment]
    assert uploaded_paths == [reviewer_file, peer_file]


def test_send_packets_default_is_anonymous(tmp_path, patch_canvas, patch_upload_attachment):
    canvas, course, assignment, subs = make_fake_canvas(ROSTER)
    patch_canvas["canvas"] = canvas

    reviewer_file = str(tmp_path / "ada_lovelace_hw.pdf")
    peer_file = str(tmp_path / "alan_turing_hw.pdf")
    (tmp_path / "ada_lovelace_hw.pdf").write_text("x")
    (tmp_path / "alan_turing_hw.pdf").write_text("x")

    assignments_csv = write_assignments_csv(
        tmp_path,
        [["Ada Lovelace", "ada@school.edu", reviewer_file, "Alan Turing", "alan@school.edu", peer_file]],
    )

    # No explicit `anonymous=` kwarg -- proving the default is double-blind.
    results = cd.send_packets(
        canvas_url="https://fake",
        token="t",
        course_id=1,
        peer_review_assignment_id=99,
        assignments_csv=assignments_csv,
        rubric_items=RUBRIC,
        live=True,
    )

    assert results[0].ok
    ada_submission = subs[1]
    _, kwargs = ada_submission.edit.call_args
    assert "Alan Turing" not in kwargs["comment"]["text_comment"]
    assert len(kwargs["comment"]["file_ids"]) == 2

    uploaded_paths = [path for _sub, path in patch_upload_attachment]
    assert reviewer_file in uploaded_paths  # own file: real name, no anonymity needed
    assert peer_file not in uploaded_paths  # peer's file must NOT be uploaded under its real name/path
    assert any(Path(p).name == "peer_submission.pdf" for p in uploaded_paths)


def test_send_packets_upload_failure_never_leaves_a_text_only_comment(tmp_path, patch_canvas, monkeypatch):
    # Simulates exactly what happened against the real network policy split:
    # Canvas's main API host is reachable but its file-storage host isn't,
    # so an upload raises partway through. No comment -- text or otherwise
    # -- should be posted in that case.
    canvas, course, assignment, subs = make_fake_canvas(ROSTER)
    patch_canvas["canvas"] = canvas

    def failing_upload(submission, path):
        raise RuntimeError("simulated: file-storage host unreachable")

    monkeypatch.setattr(cd, "_upload_attachment", failing_upload)

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
        rubric_items=RUBRIC,
        live=True,
    )

    assert not results[0].ok
    assert "unreachable" in results[0].detail
    ada_submission = subs[1]
    ada_submission.edit.assert_not_called()


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
        rubric_items=RUBRIC,
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
        rubric_items=RUBRIC,
        live=False,
    )

    assert not results[0].ok
    assert "hasn't submitted" in results[0].detail


def test_forward_reviews_skips_empty_submission(tmp_path, patch_canvas):
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
        rubric_items=RUBRIC,
        live=False,
    )

    assert not results[0].ok
    assert "Couldn't parse" in results[0].detail


def test_forward_reviews_skips_unparseable_submission(tmp_path, patch_canvas):
    # Prose with no rubric headers at all -- the parser should refuse
    # rather than guess which score belongs to which question.
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
        rubric_items=RUBRIC,
        live=False,
    )

    assert not results[0].ok
    assert "Couldn't parse" in results[0].detail
    assert "Peer Review" in results[0].detail


def make_combined_body(
    self_q1="9 - I think I nailed the edge cases.",
    self_q2="4 - could be tidier.",
    peer_q1="8 - great structure.",
    peer_q2="5 - very clean.",
):
    return (
        "Self Assessment:\n"
        f"Q1 - Correctness (out of 10):\n{self_q1}\n\n"
        f"Q2 - Code Style (out of 5):\n{self_q2}\n\n"
        "Peer Review:\n"
        f"Q1 - Correctness (out of 10):\n{peer_q1}\n\n"
        f"Q2 - Code Style (out of 5):\n{peer_q2}\n"
    )


COMBINED_BODY = make_combined_body()


def test_forward_reviews_dry_run_reports_would_post(tmp_path, patch_canvas):
    submissions = {1: SimpleNamespace(workflow_state="submitted", body=COMBINED_BODY)}
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
        rubric_items=RUBRIC,
        live=False,
    )

    assert results[0].ok
    assert "DRY RUN" in results[0].detail
    # Only the read of the reviewer's own submission happened; nothing written.
    subs[1].edit.assert_not_called()


def test_forward_reviews_live_comments_reviewers_text_identities_visible(tmp_path, patch_canvas):
    submissions = {1: SimpleNamespace(workflow_state="submitted", body=COMBINED_BODY)}
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
        rubric_items=RUBRIC,
        live=True,
        anonymous=False,
    )

    assert results[0].ok
    # Comment lands on Alan's (the reviewee's, user id 2) own submission.
    alan_submission = subs[2]
    alan_submission.edit.assert_called_once()
    _, kwargs = alan_submission.edit.call_args
    text = kwargs["comment"]["text_comment"]
    assert "Ada Lovelace" in text
    assert "Q1 - Correctness (out of 10):" in text
    assert "8 - great structure." in text
    assert "Q2 - Code Style (out of 5):" in text
    assert "5 - very clean." in text
    # The self-assessment half must never reach the reviewee.
    assert "nailed the edge cases" not in text
    assert "could be tidier" not in text


def test_forward_reviews_default_is_anonymous(tmp_path, patch_canvas):
    submissions = {1: SimpleNamespace(workflow_state="submitted", body=COMBINED_BODY)}
    canvas, course, assignment, subs = make_fake_canvas(ROSTER, submissions=submissions)
    patch_canvas["canvas"] = canvas

    assignments_csv = write_assignments_csv(
        tmp_path,
        [["Ada Lovelace", "ada@school.edu", "ada.pdf", "Alan Turing", "alan@school.edu", "alan.pdf"]],
    )

    # No explicit `anonymous=` kwarg -- proving the default is double-blind.
    results = cd.forward_reviews(
        canvas_url="https://fake",
        token="t",
        course_id=1,
        peer_review_assignment_id=99,
        assignments_csv=assignments_csv,
        rubric_items=RUBRIC,
        live=True,
    )

    assert results[0].ok
    alan_submission = subs[2]
    _, kwargs = alan_submission.edit.call_args
    text = kwargs["comment"]["text_comment"]
    assert "Ada Lovelace" not in text
    assert "8 - great structure." in text
    assert "nailed the edge cases" not in text


def test_post_announcement_dry_run_does_not_post(patch_canvas):
    canvas, course, assignment, subs = make_fake_canvas(ROSTER)
    patch_canvas["canvas"] = canvas

    result = cd.post_announcement(
        canvas_url="https://fake",
        token="t",
        course_id=1,
        peer_review_assignment_id=99,
        assignment_name="HW1 - Peer Review",
        rubric_items=RUBRIC,
        due_text="Tuesday at midnight",
        live=False,
    )

    assert result.ok
    assert "DRY RUN" in result.detail
    assert "HW1 - Peer Review" in result.detail
    assert "Due Tuesday at midnight." in result.detail
    assert "Q1" in result.detail and "Q2" in result.detail
    course.create_discussion_topic.assert_not_called()


def test_post_announcement_live_posts_generated_content(patch_canvas):
    canvas, course, assignment, subs = make_fake_canvas(ROSTER)
    patch_canvas["canvas"] = canvas
    course.create_discussion_topic.return_value = SimpleNamespace(
        title="HW1 - Peer Review — Now Available",
        html_url="https://fake/courses/1/discussion_topics/42",
    )

    result = cd.post_announcement(
        canvas_url="https://fake",
        token="t",
        course_id=1,
        peer_review_assignment_id=99,
        assignment_name="HW1 - Peer Review",
        rubric_items=RUBRIC,
        due_text="Tuesday at midnight",
        contact_line="Email me with questions.",
        live=True,
    )

    assert result.ok
    assert "discussion_topics/42" in result.detail

    _, kwargs = course.create_discussion_topic.call_args
    assert kwargs["is_announcement"] is True
    assert "HW1 - Peer Review" in kwargs["title"]
    assert "Due Tuesday at midnight." in kwargs["message"]
    assert "Email me with questions." in kwargs["message"]
    assert "https://fake/courses/1/assignments/99" in kwargs["message"]
    assert "Q1, Q2" in kwargs["message"]


def test_post_announcement_reflects_rubric_question_count(patch_canvas):
    canvas, course, assignment, subs = make_fake_canvas(ROSTER)
    patch_canvas["canvas"] = canvas

    five_questions = [
        RubricItem(question=f"Q{i}", label=f"Criterion {i}", max_points="2") for i in range(1, 6)
    ]

    result = cd.post_announcement(
        canvas_url="https://fake",
        token="t",
        course_id=1,
        peer_review_assignment_id=99,
        assignment_name="HW2 - Peer Review",
        rubric_items=five_questions,
        due_text="Friday",
        live=False,
    )

    assert "all 5 rubric" in result.detail
    assert "Q1, Q2, Q3, Q4, Q5" in result.detail


def test_post_announcement_handles_api_error(patch_canvas):
    canvas, course, assignment, subs = make_fake_canvas(ROSTER)
    patch_canvas["canvas"] = canvas
    course.create_discussion_topic.side_effect = RuntimeError("boom")

    result = cd.post_announcement(
        canvas_url="https://fake",
        token="t",
        course_id=1,
        peer_review_assignment_id=99,
        assignment_name="HW1 - Peer Review",
        rubric_items=RUBRIC,
        due_text="Tuesday at midnight",
        live=True,
    )

    assert not result.ok
    assert "Canvas API error" in result.detail
