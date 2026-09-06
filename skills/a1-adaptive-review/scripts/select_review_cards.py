#!/usr/bin/env python3
"""復習カード一覧から、指定問題数に応じた出題候補を選ぶ。"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta
import json
import math
from pathlib import Path
from zoneinfo import ZoneInfo


DEFAULT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CARDS = DEFAULT_ROOT / "復習カード" / "カード一覧.md"
BUCKET_WEIGHTS = {"deadline": 0.70, "weak": 0.20, "new_or_upcoming": 0.10}
RESULT_PRIORITY = {"incorrect": 0, "unknown": 1, "unreviewed": 2, "correct": 3}


class SelectionError(RuntimeError):
    pass


@dataclass(frozen=True)
class Card:
    card_id: str
    domain: str
    point: str
    source: str
    last_reviewed: date | None
    next_review: date
    stage: int
    last_result: str


def logical_today(now: datetime | None = None) -> date:
    current = now or datetime.now(ZoneInfo("Asia/Tokyo"))
    if current.tzinfo is None:
        current = current.replace(tzinfo=ZoneInfo("Asia/Tokyo"))
    current = current.astimezone(ZoneInfo("Asia/Tokyo"))
    return current.date() - timedelta(days=1) if current.hour < 5 else current.date()


def parse_date(value: str, card_id: str, field: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise SelectionError(f"{card_id}: {field} がYYYY-MM-DD形式ではありません: {value}") from error


def load_cards(path: Path) -> list[Card]:
    cards: list[Card] = []
    seen: set[str] = set()
    for line_number, line in enumerate(path.read_text().splitlines(), start=1):
        if not line.startswith("|") or not line.strip().startswith("| A1-"):
            continue
        values = [value.strip() for value in line.strip().strip("|").split("|")]
        if len(values) != 8:
            raise SelectionError(f"{path}:{line_number}: カード一覧の列数が8ではありません")
        card_id, domain, point, source, last_reviewed, next_review, stage, result = values
        if card_id in seen:
            raise SelectionError(f"{path}:{line_number}: Card IDが重複しています: {card_id}")
        if result not in RESULT_PRIORITY:
            raise SelectionError(f"{card_id}: 未対応のLast Resultです: {result}")
        try:
            stage_number = int(stage)
        except ValueError as error:
            raise SelectionError(f"{card_id}: Stageが整数ではありません: {stage}") from error
        cards.append(
            Card(
                card_id=card_id,
                domain=domain,
                point=point,
                source=source,
                last_reviewed=None
                if last_reviewed == "-"
                else parse_date(last_reviewed, card_id, "Last Reviewed"),
                next_review=parse_date(next_review, card_id, "Next Review"),
                stage=stage_number,
                last_result=result,
            )
        )
        seen.add(card_id)
    if not cards:
        raise SelectionError(f"カードが見つかりません: {path}")
    return cards


def allocate_quotas(count: int) -> dict[str, int]:
    if count <= 0:
        raise SelectionError("問題数は1以上で指定してください")
    exact = {bucket: count * weight for bucket, weight in BUCKET_WEIGHTS.items()}
    quotas = {bucket: math.floor(value) for bucket, value in exact.items()}
    remainder = count - sum(quotas.values())
    order = list(BUCKET_WEIGHTS)
    ranked = sorted(order, key=lambda bucket: (-(exact[bucket] - quotas[bucket]), order.index(bucket)))
    for bucket in ranked[:remainder]:
        quotas[bucket] += 1
    return quotas


def last_review_key(card: Card) -> date:
    return card.last_reviewed or date.min


def deadline_key(card: Card) -> tuple[date, int, date, str]:
    return (
        card.next_review,
        RESULT_PRIORITY[card.last_result],
        last_review_key(card),
        card.card_id,
    )


def weakness_key(card: Card) -> tuple[date, int, date, str]:
    return (
        card.next_review,
        RESULT_PRIORITY[card.last_result],
        last_review_key(card),
        card.card_id,
    )


def upcoming_key(card: Card) -> tuple[int, date, int, date, str]:
    return (
        0 if card.last_result == "unreviewed" else 1,
        card.next_review,
        RESULT_PRIORITY[card.last_result],
        last_review_key(card),
        card.card_id,
    )


def fallback_key(card: Card, on: date) -> tuple[int, date, int, date, str]:
    return (
        0 if card.next_review <= on else 1,
        card.next_review,
        RESULT_PRIORITY[card.last_result],
        last_review_key(card),
        card.card_id,
    )


def diversify(entries: list[tuple[Card, str]]) -> list[tuple[Card, str]]:
    """選出集合を変えず、同じ分野が連続しにくい順番へ並べる。"""
    remaining = list(enumerate(entries))
    ordered: list[tuple[Card, str]] = []
    domain_counts: Counter[str] = Counter()
    previous_domain: str | None = None
    while remaining:
        position, (_, entry) = min(
            enumerate(remaining),
            key=lambda item: (
                item[1][1][0].domain == previous_domain,
                domain_counts[item[1][1][0].domain],
                item[1][0],
            ),
        )
        _, chosen = remaining.pop(position)
        ordered.append(chosen)
        domain_counts[chosen[0].domain] += 1
        previous_domain = chosen[0].domain
    return ordered


def select_cards(cards: list[Card], count: int, on: date) -> dict[str, object]:
    target = min(count, len(cards))
    quotas = allocate_quotas(target)
    selected: list[tuple[Card, str]] = []
    selected_ids: set[str] = set()
    bucket_counts: Counter[str] = Counter()

    def take(candidates: list[Card], amount: int, bucket: str) -> None:
        for card in candidates:
            if bucket_counts[bucket] >= amount:
                break
            if card.card_id in selected_ids:
                continue
            selected.append((card, bucket))
            selected_ids.add(card.card_id)
            bucket_counts[bucket] += 1

    deadline = sorted((card for card in cards if card.next_review <= on), key=deadline_key)
    take(deadline, quotas["deadline"], "deadline")

    weak = sorted(
        (card for card in cards if card.last_result in {"incorrect", "unknown"}),
        key=weakness_key,
    )
    take(weak, quotas["weak"], "weak")

    upcoming = sorted(cards, key=upcoming_key)
    take(upcoming, quotas["new_or_upcoming"], "new_or_upcoming")

    if len(selected) < target:
        fallback = sorted(cards, key=lambda card: fallback_key(card, on))
        for card in fallback:
            if len(selected) >= target:
                break
            if card.card_id not in selected_ids:
                selected.append((card, "backfill"))
                selected_ids.add(card.card_id)

    selected = diversify(selected)
    return {
        "on": on.isoformat(),
        "requested_count": count,
        "selected_count": len(selected),
        "available_count": len(cards),
        "quotas": quotas,
        "actual_buckets": dict(Counter(bucket for _, bucket in selected)),
        "cards": [
            {
                "order": index,
                "card_id": card.card_id,
                "domain": card.domain,
                "point": card.point,
                "source": card.source,
                "last_reviewed": card.last_reviewed.isoformat() if card.last_reviewed else None,
                "next_review": card.next_review.isoformat(),
                "stage": card.stage,
                "last_result": card.last_result,
                "selection_bucket": bucket,
            }
            for index, (card, bucket) in enumerate(selected, start=1)
        ],
    }


def markdown(result: dict[str, object]) -> str:
    lines = [
        f"選出日: {result['on']}",
        f"指定件数: {result['requested_count']}",
        f"選出件数: {result['selected_count']} / {result['available_count']}",
        f"配分: {json.dumps(result['actual_buckets'], ensure_ascii=False, sort_keys=True)}",
        "",
        "| # | Card ID | 選出枠 | 分野 | Next Review | Last Result | Stage |",
        "|---:|---|---|---|---|---|---:|",
    ]
    for card in result["cards"]:
        lines.append(
            f"| {card['order']} | {card['card_id']} | {card['selection_bucket']} | "
            f"{card['domain']} | {card['next_review']} | {card['last_result']} | {card['stage']} |"
        )
    return "\n".join(lines) + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=20, help="選出する問題数（既定: 20）")
    parser.add_argument("--on", help="選出日 YYYY-MM-DD（省略時はJST午前5時切替）")
    parser.add_argument("--cards", type=Path, default=DEFAULT_CARDS, help="カード一覧Markdown")
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        on = date.fromisoformat(args.on) if args.on else logical_today()
        result = select_cards(load_cards(args.cards), args.count, on)
    except (OSError, SelectionError, ValueError) as error:
        raise SystemExit(f"エラー: {error}") from error
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(markdown(result), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
