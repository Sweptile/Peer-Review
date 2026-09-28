# Peer Review

Peer-review matching and delivery for a class where students submit
via Canvas → Gradescope (an LTI "External Tool" launch, so the actual
file only ever lives on Gradescope) and you grade the real submission
on Gradescope yourself. This tool handles the part neither platform
does out of the box: pairing every student with exactly one peer to
review, getting that peer's work in front of them, and — since each
student grades both their own work and their peer's — getting the
peer-review half of what they write back in front of the person it's
about.

## Why not just use Canvas's built-in peer review feature?

Canvas has one (`Require Peer Reviews` on an assignment), but it only
works when Canvas itself holds the submitted file. Since your
assignment is an External Tool launch into Gradescope, Canvas never
receives the file — there's nothing for its peer review feature to
hand out. And Gradescope has no student-facing peer-grading mode at
all (its closest feature is making someone a grader/TA on the whole
assignment, which isn't what you want here). Hence a small standalone
tool instead of leaning on either platform's native feature.

The Gradescope↔Canvas LTI link also can't be repurposed to show a
student their peer's submission — that link authenticates *one*
student into *their own* Gradescope page. Gradescope has no notion of
"let student X see student Y's file" short of grader access. So
delivery has to happen outside that link entirely — this tool posts it
as a **private submission comment** on a Canvas assignment (a "Peer
Review" text-entry assignment you create), which puts it right on the
page each student already visits to turn their review in, visible only
to them and you.

## How matching works

`peer_review.matching` builds the reviewer→reviewee mapping using
[Sattolo's algorithm](https://en.wikipedia.org/wiki/Fisher%E2%80%93Yates_shuffle#Sattolo's_algorithm),
which generates a uniformly random permutation that is a **single
cycle** over all students (student 1 reviews student 2, student 2
reviews student 3, ..., the last student reviews student 1, in some
random order). A single cycle covering everyone rules out self-review
and mutual pairs (A reviewing B *and* B reviewing A) by construction —
there's no rejection sampling needed for the base case, which is why
it's both fast and provably correct rather than "probably fine." See
`tests/test_matching.py` for the properties this is checked against,
including literally walking the cycle to confirm it has no sub-cycles
hiding inside it.

If you want to avoid repeating a pairing from a previous round (or
exclude specific pairs, e.g. project partners), pass `--avoid-repeats`
— this falls back to retrying whole cycles until one satisfies the
constraints, since removing individual edges from a cycle would break
the single-cycle guarantee.

Minimum class size is 3 — with exactly 2 students, any non-self
assignment is necessarily a mutual pair, which you asked to avoid, so
that case raises a clear error instead of silently allowing a mutual
pair or looping forever.

## What students actually submit

Each student submits ONE text-entry response to the "Peer Review"
Canvas assignment, covering both grading tasks, with two literal
section headers each on their own line:

```
Self Assessment:
<their assessment of their own work>

Peer Review:
<their review of their assigned peer's work>
```

Order doesn't matter, but the headers must appear exactly (case and
spacing are flexible — "PEER REVIEW:" and "Peer   Review :" both
match). Only the text under "Peer Review:" is ever forwarded to the
peer; the self-assessment stays between the student and you.
`send-packets` includes this exact instruction in the comment it
posts, but it's worth also putting it in the assignment's own
description in Canvas, since that's the more durable, visible place —
see `peer_review/review_parsing.py` for the parsing contract this
depends on, and why it refuses to guess (rather than forward the wrong
thing) when a submission doesn't follow it.

## The full workflow

```
Gradescope (submissions)         Canvas (roster + delivery + grading)
─────────────────────────         ──────────────────────────────────
1. Export roster + download   →   2. peer_review match
   each student's PDF                 → assignments.csv (who reviews whom)

                                   3. peer_review send-packets
                                        → each reviewer gets a private comment
                                          on their own (still-empty) "Peer
                                          Review" submission: their own file +
                                          their assigned peer's file attached,
                                          plus the Self Assessment / Peer
                                          Review format instructions

                                   4. Students submit ONE combined write-up
                                        (self-assessment + peer review, per
                                        the format above) to that "Peer
                                        Review" assignment

                                   5. peer_review forward-reviews
                                        → pulls just the "Peer Review:"
                                          section out of each reviewer's
                                          submission and comments it onto the
                                          reviewee's OWN "Peer Review"
                                          submission, so they see what was
                                          said about their work right next to
                                          their own review — their reviewer's
                                          self-assessment is never included

                                   6. You grade the "Peer Review" assignment
                                        in SpeedGrader like any other Canvas
                                        assignment — that's the "peer review
                                        score, in a different assignment"
                                        piece, and it needs no extra tooling
                                        here at all.
```

Meanwhile the student's grade on their *own* work continues to arrive
the normal way, via Gradescope → Canvas grade passback — nothing in
this tool touches that.

### Step 1 — get the inputs

- **Gradescope**: download all submissions for the assignment (zip of
  PDFs) from "Manage Submissions." Put them somewhere on disk.
- **Roster**: build `roster.csv` with columns `name,email,submission_file`
  — `submission_file` points at each student's downloaded PDF. See
  `examples/roster.csv` for the format. (You can generate this from
  Canvas's own roster export/API if that's easier than typing it by
  hand — the tool only cares about the three columns.)

### Step 2 — generate the matching

```
pip install -r requirements.txt
python -m peer_review.cli match --roster roster.csv --out assignments.csv --seed 42
```

`--seed` is optional but recommended — it makes the run reproducible
(same input, same output), which is handy if you need to regenerate
or explain a specific pairing later. Add `--avoid-repeats
previous_assignments.csv` to keep this round from repeating last
round's pairs.

Open `assignments.csv` and sanity-check it before sending anything.

### Step 3 — send packets to reviewers

Requires a Canvas API token (Canvas → Account → Settings → **New
Access Token**) and the numeric course ID (visible in the course URL).

You'll need the "Peer Review" assignment's numeric ID too (visible in
its Canvas URL) — create that assignment first (text-entry submission
type) if you haven't yet.

```
export CANVAS_API_TOKEN=...
python -m peer_review.cli send-packets \
  --assignments assignments.csv \
  --canvas-url https://yourschool.instructure.com \
  --course-id 12345 \
  --peer-review-assignment-id 67890
```

This **defaults to a dry run** — it resolves every reviewer's email
against the live Canvas roster and prints exactly what it would post,
without posting anything. Read the output, check for unmatched
emails, then re-run with `--live` to actually comment on students'
submissions. (Canvas allows comments on an assignment before a student
has submitted to it, so this works even though nobody's turned in
their review yet.)

### Step 4 — students write their reviews

Students open the "Peer Review" assignment, see the comment with their
own file and their peer's file attached, and submit their combined
self-assessment + peer review as a normal Canvas text-entry
submission, following the `Self Assessment:` / `Peer Review:` format
above. No extra tooling needed here.

### Step 5 — forward reviews to reviewees

Once reviews are in:

```
python -m peer_review.cli forward-reviews \
  --assignments assignments.csv \
  --canvas-url https://yourschool.instructure.com \
  --course-id 12345 \
  --peer-review-assignment-id 67890
```

Also dry-run by default; add `--live` to post. Reviewers who haven't
submitted yet, or whose submission doesn't contain a clean, unambiguous
"Peer Review:" section (missing, empty, or the header appears more
than once), are reported individually rather than silently skipped or
guessed at, so you can chase down stragglers and check their
submission by hand.

The forwarded review is posted on the *reviewee's own* "Peer Review"
submission, not the reviewer's — this relies on the reviewee already
having a submission object there themselves, which they do, because
under this project's matching everyone is both a reviewer for one
person and a reviewee for another.

### Step 6 — grade the reviews

In Canvas SpeedGrader, on the "Peer Review" assignment, as usual.

## Development

```
pip install -r requirements-dev.txt
pytest
```

`tests/test_matching.py` covers the matching algorithm directly (no
network). `tests/test_review_parsing.py` covers the Self Assessment /
Peer Review section splitter against Canvas's actual HTML-wrapped
submission format, including the adversarial cases (missing header,
duplicated header, HTML entities). `tests/test_canvas_distribute.py`
exercises the Canvas delivery logic (roster matching, dry-run/live
gating, unsubmitted/unparseable-submission handling) against a mocked
Canvas client — it checks the logic is right, not that the live Canvas
API calls are; that can only be confirmed against a real Canvas
instance.
