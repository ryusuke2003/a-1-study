#!/usr/bin/env python3
"""Read-only review selection: pending exclusions and exclusive 70/20/10 pools."""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta
import json
from pathlib import Path
import re
from zoneinfo import ZoneInfo

DEFAULT_ROOT = Path(__file__).resolve().parents[3]
CARDS_PATH = Path('\u5fa9\u7fd2\u30ab\u30fc\u30c9/\u30ab\u30fc\u30c9\u4e00\u89a7.md')
SESSIONS_PATH = Path('\u5b66\u7fd2\u8a18\u9332/\u5fa9\u7fd2\u554f\u984c')
DEFAULT_CARDS = DEFAULT_ROOT / CARDS_PATH
BUCKET_WEIGHTS = {'deadline': 7, 'weak': 2, 'new_or_upcoming': 1}
RESULT_PRIORITY = {'incorrect': 0, 'unknown': 1, 'unreviewed': 2, 'correct': 3}
DEFAULT_NEAR_DAYS = 3


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
    current = now or datetime.now(ZoneInfo('Asia/Tokyo'))
    if current.tzinfo is None:
        current = current.replace(tzinfo=ZoneInfo('Asia/Tokyo'))
    current = current.astimezone(ZoneInfo('Asia/Tokyo'))
    return current.date() - timedelta(days=1) if current.hour < 5 else current.date()


def parse_date(value: str, card_id: str, field: str) -> date:
    try:
        parsed = date.fromisoformat(value)
        if parsed.isoformat() != value:
            raise ValueError(value)
        return parsed
    except ValueError as error:
        raise SelectionError(f'{card_id}: {field} must use YYYY-MM-DD: {value}') from error


def load_cards(path: Path) -> list[Card]:
    cards: list[Card] = []
    seen: set[str] = set()
    for line_number, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
        if not re.match(r'^\s*\|\s*A1-', line):
            continue
        values = [value.strip() for value in line.strip().strip('|').split('|')]
        label = f'{path}:{line_number}'
        if len(values) != 8:
            raise SelectionError(f'{label}: card table must have 8 columns')
        card_id, domain, point, source, reviewed, due, stage, result = values
        if not re.fullmatch(r'A1-\d+', card_id) or card_id in seen:
            raise SelectionError(f'{label}: invalid or duplicate Card ID: {card_id}')
        if result not in RESULT_PRIORITY:
            raise SelectionError(f'{card_id}: \u672a\u5bfe\u5fdc\u306eLast Result: {result}')
        if not re.fullmatch(r'\d+', stage):
            raise SelectionError(f'{card_id}: Stage must be a non-negative integer')
        cards.append(Card(card_id, domain, point, source,
                          None if reviewed == '-' else parse_date(reviewed, card_id, 'Last Reviewed'),
                          parse_date(due, card_id, 'Next Review'), int(stage), result))
        seen.add(card_id)
    # An empty, existing card table is normal for a new repository.
    return cards


def load_pending_cards(directory: Path) -> dict[str, list[str]]:
    """Scan every Session, including answered-but-ungraded ones; never write data.

    Fail closed on ambiguous headers/IDs instead of silently permitting duplicates.
    Only exclusion metadata is parsed; question semantics remain the grader's job.
    """
    if not directory.is_dir():
        raise SelectionError(f'Session directory is missing: {directory}')
    pending: dict[str, list[str]] = {}
    for path in sorted(directory.glob('*.md')):
        if path.name == 'README.md':
            continue
        text = path.read_text(encoding='utf-8')
        starts = list(re.finditer(r'^## Session ([1-9]\d*)[ \t]*$', text, re.M))
        if not starts or len(starts) != len(re.findall(r'^## Session\b', text, re.M)):
            raise SelectionError(f'{path}: invalid or missing Session heading')
        numbers = [int(match.group(1)) for match in starts]
        if len(numbers) != len(set(numbers)):
            raise SelectionError(f'{path}: duplicate Session number')
        for i, match in enumerate(starts):
            end = starts[i + 1].start() if i + 1 < len(starts) else len(text)
            block = text[match.end():end]
            header = re.split(r'^### Q', block, maxsplit=1, flags=re.M)[0]
            status = re.findall(r'^- Status:[ \t]*(\S+)[ \t]*$', header, re.M)
            if (len(status) != 1 or len(re.findall(r'^- Status:', header, re.M)) != 1
                    or status[0] not in {'graded', 'awaiting_answers'}):
                raise SelectionError(f'{path} Session {match.group(1)}: invalid Status')
            if status[0] == 'graded':
                continue
            qstarts = list(re.finditer(r'^### Q([1-9]\d*)[ \t]*$', block, re.M))
            counts = re.findall(r'^- Question Count:[ \t]*(\d+)[ \t]*$', header, re.M)
            qnumbers = [int(q.group(1)) for q in qstarts]
            if (len(counts) != 1 or len(re.findall(r'^- Question Count:', header, re.M)) != 1
                    or int(counts[0]) < 1 or int(counts[0]) != len(qstarts)
                    or qnumbers != list(range(1, len(qstarts) + 1))
                    or len(qstarts) != len(re.findall(r'^### Q', block, re.M))):
                raise SelectionError(f'{path}: invalid pending Question Count or Q numbers')
            for j, question in enumerate(qstarts):
                qend = qstarts[j + 1].start() if j + 1 < len(qstarts) else len(block)
                qblock = block[question.end():qend]
                ids = re.findall(r'^- Card ID:[ \t]*(A1-\d+)[ \t]*$', qblock, re.M)
                if len(ids) != 1 or len(re.findall(r'^- Card ID:', qblock, re.M)) != 1:
                    raise SelectionError(f'{path}: pending question must have one Card ID')
                ref = f'{path.name}#session-{match.group(1)}'
                if ref not in pending.setdefault(ids[0], []):
                    pending[ids[0]].append(ref)
    return pending


def allocate_quotas(count: int) -> dict[str, int]:
    if type(count) is not int or count < 0:
        raise SelectionError('Quota count must be a non-negative integer')
    # Integer largest-remainder allocation, with declaration order breaking ties.
    quotas = {bucket: count * weight // 10 for bucket, weight in BUCKET_WEIGHTS.items()}
    order = list(BUCKET_WEIGHTS)
    ranked = sorted(order, key=lambda bucket: (-(count * BUCKET_WEIGHTS[bucket] % 10), order.index(bucket)))
    for bucket in ranked[:count - sum(quotas.values())]:
        quotas[bucket] += 1
    return quotas


def deadline_key(card: Card) -> tuple[date, int, date, str]:
    return card.next_review, RESULT_PRIORITY[card.last_result], card.last_reviewed or date.min, card.card_id


def upcoming_key(card: Card) -> tuple:
    return (card.last_result != 'unreviewed', *deadline_key(card))


def category(card: Card, on: date, near_days: int) -> str | None:
    """Deadline wins; weak/new labels describe exclusive selection pools, not traits."""
    if card.next_review <= on:
        return 'deadline'
    if card.last_result == 'unreviewed':
        return 'new_or_upcoming'
    if (card.next_review - on).days > near_days:
        return None
    return 'weak' if card.last_result in {'incorrect', 'unknown'} else 'new_or_upcoming'


def diversify(entries: list[tuple[Card, str, str]]) -> list[tuple[Card, str, str]]:
    """Preserve selected membership and gently interleave domains."""
    remaining = list(enumerate(entries))
    ordered: list[tuple[Card, str, str]] = []
    domain_counts: Counter[str] = Counter()
    previous_domain: str | None = None
    while remaining:
        position, _ = min(enumerate(remaining), key=lambda item: (
            item[1][1][0].domain == previous_domain,
            domain_counts[item[1][1][0].domain], item[1][0]))
        _, chosen = remaining.pop(position)
        ordered.append(chosen)
        domain_counts[chosen[0].domain] += 1
        previous_domain = chosen[0].domain
    return ordered


def select_cards(cards: list[Card], count: int, on: date, *,
                 pending_ids: set[str] | None = None,
                 near_days: int = DEFAULT_NEAR_DAYS) -> dict[str, object]:
    if type(count) is not int or count <= 0:
        raise SelectionError('Question count must be a positive integer')
    if type(near_days) is not int or near_days < 0:
        raise SelectionError('near_days must be a non-negative integer')
    if len({card.card_id for card in cards}) != len(cards):
        raise SelectionError('Duplicate Card ID in selection input')
    if any(card.last_result not in RESULT_PRIORITY for card in cards):
        raise SelectionError('Unsupported Last Result')
    pending = pending_ids or set()
    pools: dict[str, list[Card]] = {bucket: [] for bucket in BUCKET_WEIGHTS}
    excluded_pending = sorted(card.card_id for card in cards if card.card_id in pending)
    deferred: list[str] = []
    for card in cards:
        if card.card_id in pending:
            continue
        bucket = category(card, on, near_days)
        if bucket is None:
            deferred.append(card.card_id)
        else:
            pools[bucket].append(card)
    for bucket, pool in pools.items():
        pool.sort(key=upcoming_key if bucket == 'new_or_upcoming' else deadline_key)
    eligible_count = sum(map(len, pools.values()))
    target = min(count, eligible_count)
    quotas = allocate_quotas(target)
    selected: list[tuple[Card, str, str]] = []
    leftovers: list[tuple[Card, str]] = []
    for bucket, pool in pools.items():
        selected.extend((card, bucket, 'quota') for card in pool[:quotas[bucket]])
        leftovers.extend((card, bucket) for card in pool[quotas[bucket]:])
    # Backfill only eligible, not-yet-selected cards; never silently pull far-future cards.
    leftovers.sort(key=lambda item: (item[0].next_review > on, *deadline_key(item[0])))
    selected.extend((card, bucket, 'backfill') for card, bucket in leftovers[:target - len(selected)])
    selected = diversify(selected)
    return {
        'on': on.isoformat(), 'near_days': near_days,
        'requested_count': count, 'selected_count': len(selected), 'available_count': len(cards),
        'eligible_count': eligible_count, 'shortfall': count - len(selected),
        'excluded_pending_ids': excluded_pending, 'deferred_future_ids': sorted(deferred),
        'quotas': quotas,
        # Backfill is an allocation reason, NOT a fourth card category.
        'actual_buckets': dict(Counter(bucket for _, bucket, _ in selected)),
        'backfilled_count': sum(reason == 'backfill' for _, _, reason in selected),
        'selected_traits': {
            'due': sum(card.next_review <= on for card, _, _ in selected),
            'weak': sum(card.last_result in {'incorrect', 'unknown'} for card, _, _ in selected),
            'unreviewed': sum(card.last_result == 'unreviewed' for card, _, _ in selected),
        },
        'cards': [dict(order=index, card_id=card.card_id, domain=card.domain, point=card.point,
                       source=card.source,
                       last_reviewed=card.last_reviewed.isoformat() if card.last_reviewed else None,
                       next_review=card.next_review.isoformat(), stage=card.stage,
                       last_result=card.last_result, selection_bucket=bucket, selection_reason=reason)
                  for index, (card, bucket, reason) in enumerate(selected, 1)],
    }


def markdown(result: dict[str, object]) -> str:
    lines = [f"選出日: {result['on']}（近日期限: {result['near_days']}日以内）",
             f"選出: {result['selected_count']} / 指定: {result['requested_count']}; "
             f"対象: {result['eligible_count']} / 全カード: {result['available_count']}",
             f"不足: {result['shortfall']}; 未採点除外: {len(result['excluded_pending_ids'])}; "
             f"期間外: {len(result['deferred_future_ids'])}",
             f"目標枠: {json.dumps(result['quotas'], sort_keys=True)}",
             f"実際の枠: {json.dumps(result['actual_buckets'], sort_keys=True)}; "
             f"補充: {result['backfilled_count']}",
             f"性質別（重複あり）: {json.dumps(result['selected_traits'], sort_keys=True)}"]
    for ref in result.get('pending_sessions', []):
        lines.append(f'未採点Session: {ref}')
    lines += ['', '| # | Card ID | 選出枠 | 選出理由 | 分野 | Next Review | Last Result | Stage |',
              '|---:|---|---|---|---|---|---|---:|']
    for card in result['cards']:
        lines.append(f"| {card['order']} | {card['card_id']} | {card['selection_bucket']} | "
                     f"{card['selection_reason']} | {card['domain']} | {card['next_review']} | "
                     f"{card['last_result']} | {card['stage']} |")
    return '\n'.join(lines) + '\n'

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=DEFAULT_ROOT)
    parser.add_argument('--count', type=int, default=20)
    parser.add_argument('--on', help='YYYY-MM-DD; default: JST day rolls over at 05:00')
    parser.add_argument('--near-days', type=int, default=DEFAULT_NEAR_DAYS)
    parser.add_argument('--cards', type=Path, help='Custom card table; also specify --sessions')
    parser.add_argument('--sessions', type=Path, help='Directory of Session files, including pending ones')
    parser.add_argument('--format', choices=('markdown', 'json'), default='markdown')
    args = parser.parse_args()
    if args.cards is not None and args.sessions is None:
        parser.error('--cards requires --sessions to avoid reading unrelated pending Sessions')
    try:
        on = parse_date(args.on, '--on', 'date') if args.on else logical_today()
        cards = load_cards(args.cards if args.cards is not None else args.root / CARDS_PATH)
        pending = load_pending_cards(args.sessions if args.sessions is not None else args.root / SESSIONS_PATH)
        result = select_cards(cards, args.count, on, pending_ids=set(pending), near_days=args.near_days)
        result['pending_sessions'] = sorted({ref for refs in pending.values() for ref in refs})
    except (OSError, SelectionError, ValueError) as error:
        parser.exit(1, f'Error: {error}\n')
    print(json.dumps(result, ensure_ascii=False, indent=2) if args.format == 'json' else markdown(result), end='\n' if args.format == 'json' else '')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
