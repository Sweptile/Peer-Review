from __future__ import annotations

import argparse
import os
import sys

from . import matching


def _cmd_match(args: argparse.Namespace) -> int:
    students = matching.read_roster_csv(args.roster)
    avoid_pairs = matching.read_pairs_csv(args.avoid_repeats) if args.avoid_repeats else []

    try:
        assignments = matching.match_students(
            students, avoid_pairs=avoid_pairs, seed=args.seed
        )
        matching.validate_assignments(students, assignments)
    except matching.MatchingError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    matching.write_assignments_csv(args.out, assignments)
    print(f"Matched {len(students)} students -> {args.out}")
    if args.seed is not None:
        print(f"(seed={args.seed}, rerun with the same seed for the same matching)")
    return 0


def _require_token(args: argparse.Namespace) -> str | None:
    token = args.token or os.environ.get("CANVAS_API_TOKEN")
    if not token:
        print("error: no Canvas API token. Pass --token or set CANVAS_API_TOKEN.", file=sys.stderr)
    return token


def _report(results, *, live: bool, verb: str) -> int:
    failures = [r for r in results if not r.ok]
    for r in results:
        status = "OK" if r.ok else "FAILED"
        print(f"[{status}] {r.email}: {r.detail}")
    print(f"\n{len(results) - len(failures)}/{len(results)} {verb}.")
    if not live:
        print("This was a dry run -- nothing was sent. Re-run with --live to actually send it.")
    return 1 if failures else 0


def _cmd_send_packets(args: argparse.Namespace) -> int:
    from . import canvas_distribute
    from .rubric import read_rubric_csv

    token = _require_token(args)
    if not token:
        return 1

    try:
        rubric_items = read_rubric_csv(args.rubric)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    results = canvas_distribute.send_packets(
        canvas_url=args.canvas_url,
        token=token,
        course_id=args.course_id,
        peer_review_assignment_id=args.peer_review_assignment_id,
        assignments_csv=args.assignments,
        rubric_items=rubric_items,
        live=args.live,
        anonymous=args.anonymous,
    )
    return _report(results, live=args.live, verb="reviewers commented on")


def _cmd_forward_reviews(args: argparse.Namespace) -> int:
    from . import canvas_distribute
    from .rubric import read_rubric_csv

    token = _require_token(args)
    if not token:
        return 1

    try:
        rubric_items = read_rubric_csv(args.rubric)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    results = canvas_distribute.forward_reviews(
        canvas_url=args.canvas_url,
        token=token,
        course_id=args.course_id,
        peer_review_assignment_id=args.peer_review_assignment_id,
        assignments_csv=args.assignments,
        rubric_items=rubric_items,
        live=args.live,
        anonymous=args.anonymous,
    )
    return _report(results, live=args.live, verb="reviewees commented on")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="peer_review",
        description="Match students for peer review and (optionally) deliver packets via Canvas submission comments.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_match = sub.add_parser(
        "match", help="Generate a no-self, no-mutual-pair peer review matching from a roster CSV."
    )
    p_match.add_argument("--roster", required=True, help="CSV with columns: name,email[,submission_file]")
    p_match.add_argument("--out", default="assignments.csv", help="Where to write the matching CSV")
    p_match.add_argument(
        "--avoid-repeats",
        help="CSV of prior pairs (e.g. a previous assignments.csv) that must not recur",
    )
    p_match.add_argument("--seed", type=int, default=None, help="Random seed, for reproducibility")
    p_match.set_defaults(func=_cmd_match)

    p_send = sub.add_parser(
        "send-packets",
        help="Comment on each reviewer's own 'Peer Review' submission with their own file + their assigned peer's file.",
    )
    p_send.add_argument("--assignments", required=True, help="assignments.csv produced by 'match'")
    p_send.add_argument("--canvas-url", required=True, help="e.g. https://yourschool.instructure.com")
    p_send.add_argument("--course-id", required=True, type=int)
    p_send.add_argument(
        "--peer-review-assignment-id",
        required=True,
        type=int,
        help='Canvas assignment ID reviewers will submit their write-up to, e.g. the "Peer Review" assignment',
    )
    p_send.add_argument(
        "--rubric",
        required=True,
        help="CSV with columns: question,label[,max_points] -- e.g. examples/rubric.csv",
    )
    p_send.add_argument("--token", help="Canvas API token (or set CANVAS_API_TOKEN)")
    p_send.add_argument("--live", action="store_true", help="Actually post (default is dry run)")
    p_send.add_argument(
        "--anonymous",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Double-blind: don't name the peer, and anonymize their file's name/PDF metadata "
        "(default: on; use --no-anonymous for identities-visible mode)",
    )
    p_send.set_defaults(func=_cmd_send_packets)

    p_fwd = sub.add_parser(
        "forward-reviews",
        help="Pull each reviewer's submitted write-up and comment it onto the reviewee's own submission.",
    )
    p_fwd.add_argument("--assignments", required=True, help="assignments.csv produced by 'match'")
    p_fwd.add_argument("--canvas-url", required=True, help="e.g. https://yourschool.instructure.com")
    p_fwd.add_argument("--course-id", required=True, type=int)
    p_fwd.add_argument(
        "--peer-review-assignment-id",
        required=True,
        type=int,
        help="Canvas assignment ID that reviewers submitted their write-ups to",
    )
    p_fwd.add_argument(
        "--rubric",
        required=True,
        help="Same rubric CSV used with send-packets -- must match, or parsing will fail",
    )
    p_fwd.add_argument("--token", help="Canvas API token (or set CANVAS_API_TOKEN)")
    p_fwd.add_argument("--live", action="store_true", help="Actually post (default is dry run)")
    p_fwd.add_argument(
        "--anonymous",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Double-blind: don't name the reviewer when forwarding their feedback "
        "(default: on; use --no-anonymous for identities-visible mode)",
    )
    p_fwd.set_defaults(func=_cmd_forward_reviews)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
