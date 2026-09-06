#!/usr/bin/env python3
"""Summarize current review state without generating questions or writing files."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date, timedelta
import json
from pathlib import Path

from select_review_cards import (
    CARDS_PATH, DEFAULT_ROOT, SESSIONS_PATH, Card, SelectionError,
    load_cards, load_pending_cards, logical_today, parse_date,
)


def summarize(cards: list[Card], pending: dict[str, list[str]], on: date, days: int = 7) -> dict:
    """Count current state, not lifetime accuracy. Pending counts may overlap due/weak."""
    if type(days) is not int or not 1 <= days <= 366:
        raise SelectionError('days must be between 1 and 366')
    ids = {card.card_id for card in cards}
    if len(ids) != len(cards):
        raise SelectionError('Duplicate Card ID')
    unknown = set(pending) - ids
    if unknown:
        raise SelectionError('Pending Session references unknown cards: '+', '.join(sorted(unknown)))

    def counts(group: list[Card]) -> dict[str, int]:
        return {
            'total': len(group),
            'overdue': sum(c.next_review < on for c in group),
            'due_today': sum(c.next_review == on for c in group),
            'future': sum(c.next_review > on for c in group),
            'ready_due': sum(c.next_review <= on and c.card_id not in pending for c in group),
            'pending': sum(c.card_id in pending for c in group),
            'unreviewed': sum(c.last_result == 'unreviewed' for c in group),
            'weak': sum(c.last_result in {'incorrect', 'unknown'} for c in group),
        }

    scheduled = Counter(c.next_review for c in cards)
    reserved = Counter(c.next_review for c in cards if c.card_id in pending)
    domains: dict[str, list[Card]] = {}
    for card in cards:
        domains.setdefault(card.domain, []).append(card)
    return {
        'on': on.isoformat(),
        'counts': counts(cards),
        'pending_sessions': sorted({ref for refs in pending.values() for ref in refs}),
        'domains': [dict(domain=domain, **counts(group)) for domain, group in sorted(domains.items())],
        'schedule': [dict(on=(day := on+timedelta(days=i)).isoformat(), scheduled=scheduled[day],
                          pending=reserved[day], ready=scheduled[day]-reserved[day]) for i in range(days)],
        'note': 'Current card state only. Due categories partition all cards; weak/unreviewed/pending overlap. '
                'Schedule uses stored Next Review dates, not a forecast of future learning outcomes.',
    }


def markdown(report: dict) -> str:
    c = report['counts']
    lines = [f"# {report['on']} \u5fa9\u7fd2\u72b6\u6cc1", '',
             f"\u5168\u30ab\u30fc\u30c9: {c['total']} / \u671f\u9650\u8d85\u904e: {c['overdue']} / \u5f53\u65e5\u671f\u9650: {c['due_today']}",
             f"\u672a\u63a1\u70b9\u3092\u9664\u304f\u671f\u9650\u5230\u6765: {c['ready_due']} / \u672a\u63a1\u70b9: {c['pending']}",
             f"\u672a\u5fa9\u7fd2: {c['unreviewed']} / \u76f4\u8fd1\u8aa4\u7b54\u30fb\u4e0d\u660e: {c['weak']}\uff08\u671f\u9650\u3068\u306f\u91cd\u8907\u3042\u308a\uff09"]
    if report['pending_sessions']:
        lines += ['', '\u672a\u63a1\u70b9Session: ' + ', '.join(report['pending_sessions'])]
    if not c['total']:
        lines += ['', '\u5b66\u3093\u3060\u3053\u3068\u306e\u767b\u9332\u304b\u3089\u59cb\u3081\u3066\u304f\u3060\u3055\u3044\u3002']
    lines += ['', '| \u5206\u91ce | \u5168\u4ef6 | \u671f\u9650\u8d85\u904e | \u5f53\u65e5 | \u672a\u63a1\u70b9 | \u8aa4\u7b54/\u4e0d\u660e |',
              '|---|---:|---:|---:|---:|---:|']
    for d in report['domains']:
        lines.append(f"| {d['domain']} | {d['total']} | {d['overdue']} | {d['due_today']} | {d['pending']} | {d['weak']} |")
    lines += ['', '| Next Review | \u4e88\u5b9a\u6570 | \u3046\u3061\u672a\u63a1\u70b9 |', '|---|---:|---:|']
    for day in report['schedule']:
        lines.append(f"| {day['on']} | {day['scheduled']} | {day['pending']} |")
    lines += ['', '\u73fe\u5728\u306e\u8a18\u9332\u306e\u96c6\u8a08\u3067\u3059\u3002\u5168\u5c65\u6b74\u306e\u6b63\u7b54\u7387\u3084\u5b9a\u7740\u7387\u3067\u306f\u3042\u308a\u307e\u305b\u3093\u3002\u554f\u984c\u4f5c\u6210\u30fb\u72b6\u614b\u66f4\u65b0\u306f\u884c\u3044\u307e\u305b\u3093\u3002']
    return '\n'.join(lines)+'\n'


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=DEFAULT_ROOT)
    parser.add_argument('--on', help='YYYY-MM-DD; default JST 05:00 learning-day boundary')
    parser.add_argument('--days', type=int, default=7, help='Stored due-date schedule, 1-366 days')
    parser.add_argument('--format', choices=('markdown', 'json'), default='markdown')
    args = parser.parse_args()
    try:
        on = parse_date(args.on, '--on', 'date') if args.on else logical_today()
        report = summarize(load_cards(args.root/CARDS_PATH), load_pending_cards(args.root/SESSIONS_PATH), on, args.days)
    except (OSError, ValueError, SelectionError, OverflowError) as error:
        parser.exit(1, f'Error: {error}\n')
    print(json.dumps(report, ensure_ascii=False, indent=2) if args.format == 'json' else markdown(report),
          end='\n' if args.format == 'json' else '')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
