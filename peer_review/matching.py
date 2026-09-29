"""Peer-review assignment matching.

Core guarantee: every student reviews exactly one other student, is
reviewed by exactly one other student, nobody reviews themselves, and
nobody is in a mutual pair (A reviews B *and* B reviews A).

This is built on Sattolo's algorithm, which generates a uniformly
random permutation that is a single cycle over all students. A single
cycle covering everyone rules out self-loops and 2-cycles (mutual
pairs) by construction -- there's no need to generate a random
matching and then reject the bad ones. The only way to also honor a
same-group or "don't repeat last time's pairing" exclusion list is to
retry whole cycles until one satisfies the constraints, since punching
individual edges out of a cycle breaks the single-cycle property.
"""

from __future__ import annotations

import csv
import random
from dataclasses import dataclass, field
from typing import Iterable, Sequence


@dataclass(frozen=True)
class Student:
    name: str
    email: str
    submission_file: str = ""

    @property
    def key(self) -> str:
        # Emails are the stable identifier; names alone can collide.
        return self.email.strip().lower()


@dataclass(frozen=True)
class Assignment:
    reviewer: Student
    reviewee: Student


class MatchingError(ValueError):
    pass


def _sattolo_cycle(n: int, rng: random.Random) -> list[int]:
    """Return indices 0..n-1 in a uniformly random single-cycle order.

    Reading the result as consecutive pairs (result[i] -> result[i+1],
    wrapping around) gives a permutation with exactly one cycle of
    length n: no fixed points, no sub-cycles of any length < n.
    """
    order = list(range(n))
    for i in range(n - 1, 0, -1):
        j = rng.randrange(0, i)  # strictly < i -- this is what makes it Sattolo's, not Fisher-Yates
        order[i], order[j] = order[j], order[i]
    return order


def match_students(
    students: Sequence[Student],
    *,
    avoid_pairs: Iterable[frozenset] = (),
    seed: int | None = None,
    max_attempts: int = 2000,
) -> list[Assignment]:
    """Assign each student exactly one peer to review.

    avoid_pairs: an iterable of frozenset({email_a, email_b}) pairs
    that must not end up as reviewer/reviewee of each other in either
    direction (e.g. project partners, or last cycle's pairing so it
    doesn't repeat).
    """
    n = len(students)
    if n < 3:
        raise MatchingError(
            f"Need at least 3 students for a no-self, no-mutual-pair matching; got {n}. "
            "With exactly 2 students, one of them reviewing the other forces the other "
            "to review back (a mutual pair) or review nobody -- there's no way around it."
        )

    avoid = {frozenset(p) for p in avoid_pairs}
    rng = random.Random(seed)

    for _ in range(max_attempts):
        cycle = _sattolo_cycle(n, rng)
        assignments = [
            Assignment(reviewer=students[cycle[i]], reviewee=students[cycle[(i + 1) % n]])
            for i in range(n)
        ]
        if avoid and any(
            frozenset({a.reviewer.key, a.reviewee.key}) in avoid for a in assignments
        ):
            continue
        return assignments

    raise MatchingError(
        f"Could not find a valid matching honoring avoid_pairs after {max_attempts} attempts. "
        "Your exclusion list is probably too dense for the class size -- relax it."
    )


def validate_assignments(students: Sequence[Student], assignments: Sequence[Assignment]) -> None:
    """Re-derive the guarantees from scratch; raises MatchingError if violated.

    Kept separate from match_students on purpose: this is the thing a
    test (or a paranoid instructor) should call independently to
    confirm the output actually has the properties it claims, rather
    than trusting the generator's own bookkeeping.
    """
    n = len(students)
    if len(assignments) != n:
        raise MatchingError(f"Expected {n} assignments, got {len(assignments)}")

    reviewers = {a.reviewer.key for a in assignments}
    reviewees = {a.reviewee.key for a in assignments}
    if len(reviewers) != n:
        raise MatchingError("Some student is not assigned as a reviewer exactly once")
    if len(reviewees) != n:
        raise MatchingError("Some student is not reviewed exactly once")

    for a in assignments:
        if a.reviewer.key == a.reviewee.key:
            raise MatchingError(f"{a.reviewer.name} was assigned to review themselves")

    reverse = {a.reviewer.key: a.reviewee.key for a in assignments}
    for a in assignments:
        if reverse.get(a.reviewee.key) == a.reviewer.key:
            raise MatchingError(
                f"Mutual pair detected: {a.reviewer.name} <-> {a.reviewee.name}"
            )


def read_roster_csv(path: str) -> list[Student]:
    """Expects columns: name, email, and optionally submission_file."""
    students: list[Student] = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        required = {"name", "email"}
        missing = required - set(map(str.strip, reader.fieldnames or []))
        if missing:
            raise MatchingError(f"Roster CSV is missing required column(s): {sorted(missing)}")
        for row in reader:
            name = row["name"].strip()
            email = row["email"].strip()
            if not name or not email:
                continue
            students.append(
                Student(name=name, email=email, submission_file=(row.get("submission_file") or "").strip())
            )
    seen = set()
    for s in students:
        if s.key in seen:
            raise MatchingError(f"Duplicate email in roster: {s.email}")
        seen.add(s.key)
    return students


def split_by_submission(students: Sequence[Student]) -> tuple[list[Student], list[Student]]:
    """Splits a roster into (has a submission_file, doesn't).

    There's no point matching someone with no submission on file into
    a peer-review round -- there's nothing for their assigned reviewer
    to review, and nothing to hand them to review either. Returning
    the excluded list rather than just dropping it silently is the
    point: the caller should report who got left out by name, not bury
    it in a smaller roster count.
    """
    included = [s for s in students if s.submission_file]
    excluded = [s for s in students if not s.submission_file]
    return included, excluded


def write_assignments_csv(path: str, assignments: Sequence[Assignment]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
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
        for a in assignments:
            writer.writerow(
                [
                    a.reviewer.name,
                    a.reviewer.email,
                    a.reviewer.submission_file,
                    a.reviewee.name,
                    a.reviewee.email,
                    a.reviewee.submission_file,
                ]
            )


def read_pairs_csv(path: str) -> list[frozenset]:
    """Reads a CSV of email pairs to avoid re-pairing.

    A natural source for this file is a previous run's assignments.csv:
    if it sees the "reviewer_email"/"peer_email" columns that
    write_assignments_csv produces, it uses those. Otherwise it falls
    back to treating the first two columns as the pair.
    """
    pairs: list[frozenset] = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fields = reader.fieldnames or []
        if len(fields) < 2:
            raise MatchingError(f"{path} needs at least two columns of emails")
        if "reviewer_email" in fields and "peer_email" in fields:
            a_col, b_col = "reviewer_email", "peer_email"
        else:
            a_col, b_col = fields[0], fields[1]
        for row in reader:
            a, b = row[a_col].strip().lower(), row[b_col].strip().lower()
            if a and b:
                pairs.append(frozenset({a, b}))
    return pairs
