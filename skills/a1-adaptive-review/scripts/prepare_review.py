#!/usr/bin/env python3
"""Read-only question preflight and private, balanced correct-answer slots."""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import random
import re
import sys

from grade_review_session import GradingError, field, find_session, validate_session


def answer_slots(count: int = 20, seed: int | None = None) -> list[str]:
    """Balance A-D to within one slot; never use E or mutate global RNG state."""
    if not isinstance(count, int) or isinstance(count, bool) or count < 1:
        raise ValueError("count must be a positive integer")
    rng = random.Random(seed) if seed is not None else random.SystemRandom()
    slots = list("ABCD") * (count // 4) + rng.sample(list("ABCD"), count % 4)
    rng.shuffle(slots)
    return slots


def check_session(path: Path, session: int = 1) -> dict[str, int]:
    """Check publish-time syntax only, not factual accuracy or hidden hints."""
    text = path.read_text(encoding="utf-8")
    _, _, block = find_session(text, session)
    questions = validate_session(block)
    if field(block, "Status") != "awaiting_answers":
        raise GradingError("Only awaiting_answers sessions may be published")
    if any(question["selected"] is not None for question in questions):
        raise GradingError("New questions must not have preselected answers")
    ids = [question["card_id"] for question in questions]
    if len(ids) != len(set(ids)):
        raise GradingError("Do not repeat a Card ID within a new Session")
    # This catches common metadata leaks, not semantic hints in natural language.
    forbidden = re.compile(
        r"^(?:#{2,6} (?:採点|解説|模範解答)"
        r"|(?:- )?(?:Score|Result|Correct Answer|correct_answer|正解)\s*:)"
        r"|<!--[\s\S]*?(?:Correct Answer|correct_answer|正解)",
        re.MULTILINE,
    )
    if forbidden.search(block):
        raise GradingError("Remove grading, explanations and answer keys before publication")
    return {"session": session, "questions": len(questions), "unique_cards": len(set(ids))}


def positive_int(value: str) -> int:
    try:
        number = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be a positive integer") from error
    if number < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return number


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    slots = subparsers.add_parser("answer-slots", help="Private A-D placement plan; do not publish")
    slots.add_argument("--count", type=positive_int, default=20)
    slots.add_argument("--seed", type=int, help="Use only when a reproducible plan is needed")
    check = subparsers.add_parser("check", help="Validate a new Session without modifying it")
    check.add_argument("file", type=Path)
    check.add_argument("--session", type=positive_int, default=1)
    args = parser.parse_args()
    try:
        if args.command == "answer-slots":
            plan = answer_slots(args.count, args.seed)
            counts = Counter(plan)
            result = {"slots": plan, "counts": {c: counts[c] for c in "ABCD"}}
        else:
            result = check_session(args.file, args.session)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (GradingError, OSError, ValueError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
