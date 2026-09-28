import csv

import pytest

from peer_review.matching import (
    Assignment,
    MatchingError,
    Student,
    match_students,
    read_pairs_csv,
    read_roster_csv,
    validate_assignments,
    write_assignments_csv,
)


def make_students(n: int) -> list[Student]:
    return [Student(name=f"Student {i}", email=f"s{i}@school.edu") for i in range(n)]


@pytest.mark.parametrize("n", [3, 4, 5, 10, 37, 100])
def test_valid_matching_for_various_sizes(n):
    students = make_students(n)
    assignments = match_students(students, seed=42)
    validate_assignments(students, assignments)  # raises on any violation


def test_matching_is_a_single_cycle_not_just_pairwise_valid():
    # A stronger check than validate_assignments: walk the cycle starting
    # from student 0 and confirm it visits every student exactly once
    # before returning -- i.e. there's no sub-cycle hiding inside it.
    students = make_students(9)
    assignments = match_students(students, seed=7)
    by_reviewer = {a.reviewer.key: a.reviewee.key for a in assignments}

    visited = []
    current = students[0].key
    for _ in range(len(students)):
        visited.append(current)
        current = by_reviewer[current]

    assert len(set(visited)) == len(students)
    assert current == students[0].key  # cycle closes back to the start


def test_no_self_review():
    students = make_students(20)
    assignments = match_students(students, seed=1)
    for a in assignments:
        assert a.reviewer.key != a.reviewee.key


def test_no_mutual_pairs():
    students = make_students(20)
    assignments = match_students(students, seed=1)
    reverse = {a.reviewer.key: a.reviewee.key for a in assignments}
    for a in assignments:
        assert reverse[a.reviewee.key] != a.reviewer.key


def test_same_seed_is_reproducible():
    students = make_students(15)
    a1 = match_students(students, seed=123)
    a2 = match_students(students, seed=123)
    assert [(a.reviewer.key, a.reviewee.key) for a in a1] == [
        (a.reviewer.key, a.reviewee.key) for a in a2
    ]


def test_different_seeds_usually_differ():
    students = make_students(15)
    a1 = match_students(students, seed=1)
    a2 = match_students(students, seed=2)
    assert [(a.reviewer.key, a.reviewee.key) for a in a1] != [
        (a.reviewer.key, a.reviewee.key) for a in a2
    ]


def test_two_students_is_impossible():
    with pytest.raises(MatchingError):
        match_students(make_students(2), seed=0)


def test_avoid_pairs_is_respected():
    students = make_students(5)
    # Force a collision-prone scenario: forbid every pair so the matcher
    # has to exhaust its retry budget and raise, proving avoid_pairs
    # actually constrains the output rather than being silently ignored.
    all_pairs = [
        frozenset({students[i].key, students[j].key})
        for i in range(5)
        for j in range(i + 1, 5)
    ]
    with pytest.raises(MatchingError):
        match_students(students, avoid_pairs=all_pairs, seed=3, max_attempts=50)


def test_avoid_pairs_excludes_specific_prior_pairing():
    students = make_students(6)
    first = match_students(students, seed=10)
    forbidden = {frozenset({a.reviewer.key, a.reviewee.key}) for a in first}

    second = match_students(students, avoid_pairs=forbidden, seed=11)
    second_pairs = {frozenset({a.reviewer.key, a.reviewee.key}) for a in second}
    assert forbidden.isdisjoint(second_pairs)


def test_validate_assignments_catches_self_review():
    students = make_students(4)
    bad = [Assignment(reviewer=students[0], reviewee=students[0])] + [
        Assignment(reviewer=students[i], reviewee=students[(i + 1) % 4]) for i in range(1, 4)
    ]
    with pytest.raises(MatchingError):
        validate_assignments(students, bad)


def test_validate_assignments_catches_mutual_pair():
    students = make_students(4)
    bad = [
        Assignment(reviewer=students[0], reviewee=students[1]),
        Assignment(reviewer=students[1], reviewee=students[0]),
        Assignment(reviewer=students[2], reviewee=students[3]),
        Assignment(reviewer=students[3], reviewee=students[2]),
    ]
    with pytest.raises(MatchingError):
        validate_assignments(students, bad)


def test_read_roster_csv(tmp_path):
    path = tmp_path / "roster.csv"
    path.write_text("name,email,submission_file\nAda Lovelace,ada@school.edu,ada.pdf\n")
    students = read_roster_csv(str(path))
    assert students == [Student(name="Ada Lovelace", email="ada@school.edu", submission_file="ada.pdf")]


def test_read_roster_csv_rejects_duplicate_emails(tmp_path):
    path = tmp_path / "roster.csv"
    path.write_text("name,email\nAda,ada@school.edu\nAda2,ADA@school.edu\n")
    with pytest.raises(MatchingError):
        read_roster_csv(str(path))


def test_read_roster_csv_missing_column(tmp_path):
    path = tmp_path / "roster.csv"
    path.write_text("name\nAda\n")
    with pytest.raises(MatchingError):
        read_roster_csv(str(path))


def test_write_and_reread_assignments_roundtrip(tmp_path):
    students = make_students(5)
    assignments = match_students(students, seed=99)
    out = tmp_path / "assignments.csv"
    write_assignments_csv(str(out), assignments)

    with open(out, newline="") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 5
    assert set(rows[0].keys()) == {
        "reviewer_name",
        "reviewer_email",
        "reviewer_submission_file",
        "peer_name",
        "peer_email",
        "peer_submission_file",
    }


def test_read_pairs_csv_prefers_named_columns_from_assignments_output(tmp_path):
    students = make_students(5)
    assignments = match_students(students, seed=5)
    out = tmp_path / "assignments.csv"
    write_assignments_csv(str(out), assignments)

    pairs = read_pairs_csv(str(out))
    expected = {frozenset({a.reviewer.key, a.reviewee.key}) for a in assignments}
    assert set(pairs) == expected
