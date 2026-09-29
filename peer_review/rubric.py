"""The rubric definition for per-question peer review.

Loaded once from a small CSV (question,label,max_points) that you
write for a given assignment. It drives two things: the exact
submission template `send-packets` tells students to follow (so they
know exactly which headers to type and in what order), and how
`forward-reviews` parses their answers back out (see
review_parsing.py) -- both read from the same rubric definition, so
there's one place to update if the rubric changes.

`question` is the literal marker students must type on its own line
(e.g. "Q1"), not the human-readable name. Using a short fixed id
instead of asking students to retype the exact criterion name avoids
typo-driven parse failures on a name that might be long or reworded.
`label` and `max_points` are only used for display, in the template
and in what gets reconstructed for the reviewee -- they're pulled from
the rubric definition, not from whatever the student actually typed,
so the reviewee always sees a consistently formatted header regardless
of how closely the reviewer matched the instructions.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass


@dataclass(frozen=True)
class RubricItem:
    question: str
    label: str
    max_points: str = ""


def read_rubric_csv(path: str) -> list[RubricItem]:
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        required = {"question", "label"}
        missing = required - set(map(str.strip, reader.fieldnames or []))
        if missing:
            raise ValueError(f"Rubric CSV is missing required column(s): {sorted(missing)}")

        items = []
        for row in reader:
            question = row["question"].strip()
            label = row["label"].strip()
            max_points = (row.get("max_points") or "").strip()
            if not question or not label:
                continue
            items.append(RubricItem(question=question, label=label, max_points=max_points))

    if not items:
        raise ValueError(f"{path} has no rubric items")

    seen = set()
    for item in items:
        key = item.question.lower()
        if key in seen:
            raise ValueError(f"Duplicate question id in rubric: {item.question!r}")
        seen.add(key)

    return items


def format_question_header(item: RubricItem) -> str:
    points = f" (out of {item.max_points})" if item.max_points else ""
    return f"{item.question} - {item.label}{points}:"


def template_for(items: list[RubricItem]) -> str:
    """The exact block a student should fill in for one half (self or peer)."""
    return "\n\n".join(
        f"{format_question_header(item)}\n<your score and justification>" for item in items
    )
