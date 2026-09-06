from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import random
import subprocess
import sys
import tempfile
import unittest

from test_grade_review_session import RepositoryFixture

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills/a1-adaptive-review/scripts'
sys.path.insert(0, str(SCRIPTS))
try:
    import prepare_review as PREP
finally:
    sys.path.pop(0)


class AnswerSlotsTest(unittest.TestCase):
    def test_balance_for_two_thousand_deterministic_plans(self) -> None:
        for count in range(1, 201):
            for seed in range(10):
                with self.subTest(count=count, seed=seed):
                    slots = PREP.answer_slots(count, seed)
                    self.assertEqual(count, len(slots))
                    self.assertLessEqual(set(slots), set('ABCD'))
                    counts = Counter(slots)
                    values = [counts[c] for c in 'ABCD']
                    self.assertLessEqual(max(values) - min(values), 1)

    def test_default_twenty_questions(self) -> None:
        self.assertEqual(Counter(dict.fromkeys('ABCD', 5)), Counter(PREP.answer_slots()))

    def test_seed_is_reproducible(self) -> None:
        self.assertEqual(PREP.answer_slots(49, 42), PREP.answer_slots(49, 42))

    def test_seed_does_not_alter_global_rng(self) -> None:
        before = random.getstate()
        PREP.answer_slots(20, 42)
        self.assertEqual(before, random.getstate())

    def test_remainders_do_not_always_favor_a(self) -> None:
        seen = {PREP.answer_slots(1, seed)[0] for seed in range(40)}
        self.assertEqual(set('ABCD'), seen)

    def test_invalid_counts(self) -> None:
        for count in (0, -1, 0.5, True, '20'):
            with self.subTest(count=count), self.assertRaises(ValueError):
                PREP.answer_slots(count)

    def test_cli_json_and_bad_count(self) -> None:
        command = [sys.executable, str(SCRIPTS / 'prepare_review.py'), 'answer-slots']
        good = subprocess.run(command + ['--count', '30', '--seed', '7'], capture_output=True, text=True)
        self.assertEqual(0, good.returncode, good.stderr)
        self.assertEqual(30, len(json.loads(good.stdout)['slots']))
        bad = subprocess.run(command + ['--count', '0'], capture_output=True, text=True)
        self.assertNotEqual(0, bad.returncode)


class PreflightTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.fixture = RepositoryFixture(self.root, 2, selected='')
        self.path = self.fixture.session

    def test_valid_session_read_only(self) -> None:
        before = {p: p.read_bytes() for p in self.root.rglob('*.md')}
        self.assertEqual({'session': 1, 'questions': 2, 'unique_cards': 2}, PREP.check_session(self.path))
        self.assertEqual(before, {p: p.read_bytes() for p in self.root.rglob('*.md')})

    def test_rejects_duplicate_card(self) -> None:
        self.path.write_text(self.path.read_text().replace('A1-0002', 'A1-0001'))
        with self.assertRaises(PREP.GradingError):
            PREP.check_session(self.path)

    def test_rejects_selected_answer(self) -> None:
        self.path.write_text(self.path.read_text().replace('- [ ] A.', '- [x] A.', 1))
        with self.assertRaises(PREP.GradingError):
            PREP.check_session(self.path)

    def test_rejects_answer_key_and_grading_leaks(self) -> None:
        original = self.path.read_text()
        for leak in ('\nCorrect Answer: A\n', '\n### 採点\n',
                     '\n### 解説\n', '\n<!-- correct_answer: A -->\n'):
            with self.subTest(leak=leak):
                self.path.write_text(original + leak)
                with self.assertRaises(PREP.GradingError):
                    PREP.check_session(self.path)

    def test_rejects_graded_session(self) -> None:
        self.path.write_text(self.path.read_text().replace('awaiting_answers', 'graded'))
        with self.assertRaises(PREP.GradingError):
            PREP.check_session(self.path)

    def test_rejects_wrong_count(self) -> None:
        self.path.write_text(self.path.read_text().replace('Question Count: 2', 'Question Count: 20'))
        with self.assertRaises(PREP.GradingError):
            PREP.check_session(self.path)

    def test_cli_check(self) -> None:
        result = subprocess.run([sys.executable, str(SCRIPTS / 'prepare_review.py'),
                                 'check', str(self.path)], capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(2, json.loads(result.stdout)['questions'])


if __name__ == '__main__':
    unittest.main()
