"""Regression tests for pool semantics, pending reservations and bounded lookahead."""
from __future__ import annotations

from collections import Counter
from datetime import date, timedelta, datetime, timezone
import json
from pathlib import Path
import random
import subprocess
import sys
import tempfile
import unittest

from test_select_review_cards import SELECTOR as S, card

ON = date(2026, 9, 6)
SCRIPT = Path(S.__file__)


def session(number: int, ids: list[str], status: str = 'awaiting_answers') -> str:
    header = f'## Session {number}\n\n- Status: {status}\n- Question Count: {len(ids)}\n\n'
    return header + '\n'.join(f'### Q{i}\n\n- Card ID: {cid}\n\n- [x] A. Answered\n'
                               for i, cid in enumerate(ids, 1))


class SelectionPolicyTest(unittest.TestCase):
    def test_due_weak_and_new_belong_only_to_deadline(self) -> None:
        for result in ('incorrect', 'unknown', 'unreviewed', 'correct'):
            with self.subTest(result=result):
                self.assertEqual('deadline', S.category(card(1, ON.isoformat(), result), ON, 3))

    def test_overdue_leftovers_are_not_relabeled_new_or_weak(self) -> None:
        cards = [card(i, '2026-09-01', 'incorrect') for i in range(1, 31)]
        result = S.select_cards(cards, 20, ON)
        self.assertEqual({'deadline': 20}, result['actual_buckets'])
        self.assertEqual(6, result['backfilled_count'])
        self.assertEqual(20, result['selected_traits']['weak'])

    def test_quotas_are_not_trait_caps(self) -> None:
        cards = ([card(i, '2026-09-05', 'incorrect') for i in range(1, 15)]
                 + [card(i, '2026-09-07', 'incorrect') for i in range(15, 19)]
                 + [card(i, '2026-09-08', 'unreviewed') for i in range(19, 21)])
        result = S.select_cards(cards, 20, ON)
        self.assertEqual({'deadline': 14, 'weak': 4, 'new_or_upcoming': 2}, result['actual_buckets'])
        self.assertEqual({'due': 14, 'weak': 18, 'unreviewed': 2}, result['selected_traits'])

    def test_near_boundary_is_inclusive(self) -> None:
        for status, bucket in [('correct', 'new_or_upcoming'), ('unknown', 'weak')]:
            self.assertEqual(bucket, S.category(card(1, '2026-09-09', status), ON, 3))
            self.assertIsNone(S.category(card(1, '2026-09-10', status), ON, 3))

    def test_far_future_is_not_backfill_even_when_every_slot_is_empty(self) -> None:
        cards = [card(1, '2026-10-20', 'correct'), card(2, '2026-10-20', 'incorrect')]
        result = S.select_cards(cards, 20, ON)
        self.assertEqual(0, result['selected_count'])
        self.assertEqual(20, result['shortfall'])
        self.assertEqual(['A1-0001', 'A1-0002'], result['deferred_future_ids'])

    def test_near_window_is_configurable_including_zero(self) -> None:
        cards = [card(1, '2026-09-07', 'correct'), card(2, '2026-09-10', 'unknown')]
        self.assertEqual(0, S.select_cards(cards, 20, ON, near_days=0)['selected_count'])
        self.assertEqual(2, S.select_cards(cards, 20, ON, near_days=4)['selected_count'])

    def test_new_cards_are_not_future_review_deferrals(self) -> None:
        result = S.select_cards([card(1, '2026-10-20', 'unreviewed')], 1, ON)
        self.assertEqual({'new_or_upcoming': 1}, result['actual_buckets'])

    def test_pending_is_excluded_before_quotas(self) -> None:
        cards = [card(i, '2026-09-01', 'correct') for i in range(1, 11)]
        result = S.select_cards(cards, 20, ON, pending_ids={f'A1-{i:04d}' for i in range(1, 9)})
        self.assertEqual(2, result['eligible_count'])
        self.assertEqual(S.allocate_quotas(2), result['quotas'])
        self.assertEqual({'A1-0009', 'A1-0010'}, {c['card_id'] for c in result['cards']})
        self.assertEqual(18, result['shortfall'])

    def test_pending_is_not_backfilled(self) -> None:
        cards = [card(i, '2026-09-01', 'unknown') for i in range(1, 6)]
        result = S.select_cards(cards, 5, ON, pending_ids={'A1-0001'})
        self.assertEqual(4, result['selected_count'])
        self.assertNotIn('A1-0001', [c['card_id'] for c in result['cards']])

    def test_all_pending_and_empty_inputs_return_zero_not_error(self) -> None:
        for cards, pending in [([], set()), ([card(1, '2026-09-01', 'correct')], {'A1-0001'})]:
            result = S.select_cards(cards, 20, ON, pending_ids=pending)
            self.assertEqual([], result['cards'])
            self.assertEqual(0, sum(result['quotas'].values()))
            self.assertEqual(20, result['shortfall'])

    def test_invalid_counts_and_windows(self) -> None:
        for count in (0, -1, True, 1.5, '20'):
            with self.subTest(count=count), self.assertRaises(S.SelectionError):
                S.select_cards([], count, ON)
        for near_days in (-1, True, 1.5, '3'):
            with self.subTest(near_days=near_days), self.assertRaises(S.SelectionError):
                S.select_cards([], 20, ON, near_days=near_days)

    def test_duplicate_input_ids_fail(self) -> None:
        c = card(1, '2026-09-01', 'correct')
        with self.assertRaises(S.SelectionError):
            S.select_cards([c, c], 2, ON)

    def test_integer_quota_totals_for_10001_counts(self) -> None:
        for count in range(10001):
            quotas = S.allocate_quotas(count)
            self.assertEqual(count, sum(quotas.values()))
            for name, weight in S.BUCKET_WEIGHTS.items():
                self.assertLess(abs(quotas[name] * 10 - count * weight), 10)

    def test_selection_invariants_for_300_random_inputs(self) -> None:
        for seed in range(300):
            rng = random.Random(seed)
            cards = [card(i, (ON + timedelta(days=rng.randint(-10, 60))).isoformat(),
                          rng.choice(list(S.RESULT_PRIORITY)), domain=f'D{rng.randrange(4)}')
                     for i in range(1, rng.randint(1, 120))]
            pending = {c.card_id for c in cards if rng.random() < 0.25}
            count = rng.randint(1, 100)
            result = S.select_cards(cards, count, ON, pending_ids=pending)
            eligible = {c.card_id: c for c in cards if c.card_id not in pending and S.category(c, ON, 3)}
            self.assertEqual(min(count, len(eligible)), result['selected_count'])
            ids = [c['card_id'] for c in result['cards']]
            self.assertEqual(len(ids), len(set(ids)))
            self.assertTrue(set(ids) <= set(eligible))
            self.assertEqual(Counter(c['selection_bucket'] for c in result['cards']), result['actual_buckets'])
            for entry in result['cards']:
                self.assertEqual(S.category(eligible[entry['card_id']], ON, 3), entry['selection_bucket'])
            original = list(cards)
            rng.shuffle(cards)
            self.assertEqual(result, S.select_cards(cards, count, ON, pending_ids=pending))
            self.assertEqual(set(original), set(cards))

    def test_utc_and_naive_jst_boundary(self) -> None:
        self.assertEqual(date(2026, 9, 6), S.logical_today(datetime(2026, 9, 6, 19, 59, tzinfo=timezone.utc)))
        self.assertEqual(date(2026, 9, 7), S.logical_today(datetime(2026, 9, 6, 20, 0, tzinfo=timezone.utc)))
        self.assertEqual(date(2026, 9, 6), S.logical_today(datetime(2026, 9, 7, 4, 59)))


class PendingSessionTest(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.directory = Path(temp.name)

    def test_multiple_dates_and_sessions_including_answered_pending(self) -> None:
        (self.directory / '2026-09-01.md').write_text(
            session(1, ['A1-0001'], 'graded') + session(2, ['A1-0002', 'A1-0003']))
        (self.directory / '2026-09-06.md').write_text(session(1, ['A1-0003', 'A1-0004']))
        (self.directory / 'README.md').write_text('Not a Session')
        pending = S.load_pending_cards(self.directory)
        self.assertEqual({'A1-0002', 'A1-0003', 'A1-0004'}, set(pending))
        self.assertEqual(['2026-09-01.md#session-2', '2026-09-06.md#session-1'], pending['A1-0003'])

    def test_graded_card_is_released(self) -> None:
        path = self.directory / '2026-09-06.md'
        path.write_text(session(1, ['A1-0001']))
        self.assertIn('A1-0001', S.load_pending_cards(self.directory))
        path.write_text(session(1, ['A1-0001'], 'graded'))
        self.assertEqual({}, S.load_pending_cards(self.directory))

    def test_read_only_and_crlf(self) -> None:
        path = self.directory / '2026-09-06.md'
        data = session(1, ['A1-0001']).replace('\n', '\r\n').encode()
        path.write_bytes(data)
        self.assertIn('A1-0001', S.load_pending_cards(self.directory))
        self.assertEqual(data, path.read_bytes())

    def test_empty_directory_is_valid_but_missing_is_error(self) -> None:
        self.assertEqual({}, S.load_pending_cards(self.directory))
        with self.assertRaises(S.SelectionError):
            S.load_pending_cards(self.directory / 'missing')

    def test_malformed_pending_fails_closed(self) -> None:
        good = session(1, ['A1-0001'])
        mutations = [
            '', good.replace('awaiting_answers', 'waiting'),
            good.replace('- Status: awaiting_answers', ''),
            good.replace('- Status: awaiting_answers', '- Status: graded\n- Status: awaiting_answers'),
            good + good, good.replace('Question Count: 1', 'Question Count: 2'),
            good.replace('Q1', 'Q2'), good.replace('Q1', 'Qx'),
            good.replace('Session 1', 'Session 0'), good.replace('- Card ID: A1-0001', ''),
            good.replace('- Card ID: A1-0001', '- Card ID: A1-0001\n- Card ID: wrong'),
            good.replace('A1-0001', 'invalid'),
        ]
        for text in mutations:
            with self.subTest(text=text):
                (self.directory / '2026-09-06.md').write_text(text)
                with self.assertRaises(S.SelectionError):
                    S.load_pending_cards(self.directory)


class SelectorCliTest(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.sessions = self.root / S.SESSIONS_PATH
        self.sessions.mkdir(parents=True)
        self.cards = self.root / S.CARDS_PATH
        self.cards.parent.mkdir(parents=True)
        self.cards.write_text('| A1-0001 | D | Point | [N](n.md) | - | 2026-09-01 | 0 | unreviewed |\n'
                              '| A1-0002 | D | Point | [N](n.md) | - | 2026-09-01 | 0 | unreviewed |\n')

    def run_cli(self, *args: str):
        return subprocess.run([sys.executable, str(SCRIPT), '--root', str(self.root),
                               '--on', '2026-09-06', *args], cwd=self.root,
                              capture_output=True, text=True)

    def test_cli_excludes_pending_and_never_writes(self) -> None:
        (self.sessions / '2026-09-06.md').write_text(session(1, ['A1-0001']))
        before = {p: p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        process = self.run_cli('--format', 'json')
        self.assertEqual(0, process.returncode, process.stderr)
        result = json.loads(process.stdout)
        self.assertEqual(['A1-0002'], [c['card_id'] for c in result['cards']])
        self.assertEqual(['2026-09-06.md#session-1'], result['pending_sessions'])
        self.assertEqual(before, {p: p.read_bytes() for p in self.root.rglob('*') if p.is_file()})

    def test_custom_cards_requires_paired_sessions(self) -> None:
        self.assertNotEqual(0, self.run_cli('--cards', str(self.cards)).returncode)
        paired = self.run_cli('--cards', str(self.cards), '--sessions', str(self.sessions), '--format', 'json')
        self.assertEqual(0, paired.returncode, paired.stderr)
        self.assertEqual(2, json.loads(paired.stdout)['selected_count'])

    def test_markdown_explains_shortfall_and_pending(self) -> None:
        (self.sessions / '2026-09-06.md').write_text(session(1, ['A1-0001', 'A1-0002']))
        process = self.run_cli()
        self.assertEqual(0, process.returncode, process.stderr)
        self.assertIn('不足: 20', process.stdout)
        self.assertIn('2026-09-06.md#session-1', process.stdout)

    def test_bad_date_count_and_window_fail(self) -> None:
        for args in [('--on', '2026-99-99'), ('--count', '0'), ('--near-days', '-1')]:
            with self.subTest(args=args):
                self.assertNotEqual(0, self.run_cli(*args).returncode)

    def test_empty_table_returns_empty_result(self) -> None:
        self.cards.write_text('| Card ID | Domain |\n')
        process = self.run_cli('--format', 'json')
        self.assertEqual(0, process.returncode, process.stderr)
        self.assertEqual(0, json.loads(process.stdout)['selected_count'])

    def test_loader_rejects_duplicate_invalid_ids_and_negative_stage(self) -> None:
        good = self.cards.read_text()
        for text in [good + good, good.replace('A1-0001', 'A1-bad'), good.replace('| 0 |', '| -1 |'),
                     good.replace('2026-09-01', '2026-99-99')]:
            with self.subTest(text=text):
                self.cards.write_text(text)
                self.assertNotEqual(0, self.run_cli().returncode)


if __name__ == '__main__':
    unittest.main()
