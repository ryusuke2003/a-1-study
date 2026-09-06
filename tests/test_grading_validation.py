"""Regression checks for validation gaps found in the September review."""
from __future__ import annotations

import json
from pathlib import Path
import re
import shutil
import tempfile
import unittest

from test_grade_review_session import GRADER, RepositoryFixture, explanation


class GradingValidationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.fixture = RepositoryFixture(self.root, 1)
        self.file = self.fixture.session
        self.relative = self.file.relative_to(self.root).as_posix()

    def manifest(self) -> dict:
        data = GRADER.make_draft(self.root, self.relative, 1, '2026-09-02')
        for entry in data['questions']:
            entry['correct_answer'] = 'A'
            entry['result'] = 'correct'
        return data

    def apply(self, data: dict, dry_run: bool = True):
        path = self.root / 'manifest.json'
        path.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
        return GRADER.apply_manifest(self.root, path, dry_run=dry_run)

    def replace(self, old: str, new: str) -> None:
        text = self.file.read_text(encoding='utf-8')
        self.assertIn(old, text)
        self.file.write_text(text.replace(old, new), encoding='utf-8')

    def assert_prepare_rejected(self) -> None:
        with self.assertRaises(GRADER.GradingError):
            GRADER.make_draft(self.root, self.relative, 1, '2026-09-02')

    def test_apply_rejects_wrong_question_count_without_digest(self) -> None:
        data = self.manifest()
        data.pop('session_sha256', None)  # Legacy manifests must also be validated.
        self.replace('Question Count: 1', 'Question Count: 2')
        before = self.file.read_bytes()
        with self.assertRaises(GRADER.GradingError):
            self.apply(data)
        self.assertEqual(before, self.file.read_bytes())

    def test_apply_rejects_empty_session(self) -> None:
        self.file.write_text('## Session 1\n\n- Status: awaiting_answers\n- Question Count: 0\n')
        with self.assertRaises(GRADER.GradingError):
            self.apply({'session_file': self.relative, 'session': 1,
                        'graded_on': '2026-09-02', 'questions': []})

    def test_prepare_rejects_duplicate_option(self) -> None:
        self.replace('- [ ] B.', '- [ ] A. duplicate\n- [ ] B.')
        self.assert_prepare_rejected()

    def test_prepare_rejects_missing_e(self) -> None:
        self.file.write_text(re.sub(r'^- \[ \] E\..*\n', '', self.file.read_text(), flags=re.M))
        self.assert_prepare_rejected()

    def test_prepare_rejects_invalid_e_text(self) -> None:
        self.replace('E. わかりません', 'E. Another answer')
        self.assert_prepare_rejected()

    def test_prepare_rejects_extra_f_option(self) -> None:
        self.file.write_text(self.file.read_text() + '\n- [ ] F. Extra\n')
        self.assert_prepare_rejected()

    def test_prepare_rejects_missing_stage(self) -> None:
        self.replace('- Stage: 0\n', '')
        self.assert_prepare_rejected()

    def test_prepare_rejects_missing_problem(self) -> None:
        self.replace('### 問題', '### Unexpected heading')
        self.assert_prepare_rejected()

    def test_prepare_rejects_missing_card_id(self) -> None:
        self.replace('- Card ID: A1-0001\n', '')
        self.assert_prepare_rejected()

    def test_prepare_rejects_duplicate_session_number(self) -> None:
        self.file.write_text(self.file.read_text() + '\n' + self.file.read_text())
        self.assert_prepare_rejected()

    def test_prepare_rejects_duplicate_count_field(self) -> None:
        self.replace('- Question Count: 1', '- Question Count: 1\n- Question Count: 20')
        self.assert_prepare_rejected()

    def test_apply_rejects_changed_question_since_prepare(self) -> None:
        data = self.manifest()
        self.replace('A1-0001の正しい説明', 'Changed answer content')
        with self.assertRaises(GRADER.GradingError):
            self.apply(data)

    def test_legacy_manifest_still_works_when_valid(self) -> None:
        data = self.manifest()
        data.pop('session_sha256', None)
        self.assertEqual(1, self.apply(data)['correct'])

    def test_dry_run_never_writes_study_files(self) -> None:
        data = self.manifest()
        before = {p: p.read_bytes() for p in self.root.rglob('*.md')}
        self.assertEqual(1, self.apply(data)['correct'])
        self.assertEqual(before, {p: p.read_bytes() for p in self.root.rglob('*.md')})

    def test_real_verifier_accepts_correct_incorrect_unknown(self) -> None:
        for selected, result in [('A', 'correct'), ('B', 'incorrect'), ('E', 'unknown'), ('', 'unknown')]:
            with self.subTest(selected=selected), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                fixture = RepositoryFixture(root, 1, selected=selected)
                scripts = root / 'skills/a1-adaptive-review/scripts'
                original = Path(__file__).resolve().parents[1] / 'skills/a1-adaptive-review/scripts/verify_review_sessions.py'
                shutil.copy2(original, scripts / original.name)
                data = GRADER.make_draft(root, fixture.session.relative_to(root).as_posix(), 1, '2026-09-02')
                entry = data['questions'][0]
                entry.update(correct_answer='A', result=result)
                if result != 'correct':
                    entry['explanations'] = {c: explanation(c) for c in 'ABCD'}
                path = root / 'manifest.json'
                path.write_text(json.dumps(data, ensure_ascii=False))
                self.assertEqual(1, GRADER.apply_manifest(root, path)[result])

    def test_forty_nine_questions_remain_supported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = RepositoryFixture(root, 49)
            data = GRADER.make_draft(root, fixture.session.relative_to(root).as_posix(), 1, '2026-09-02')
            for entry in data['questions']:
                entry.update(correct_answer='A', result='correct')
            path = root / 'manifest.json'
            path.write_text(json.dumps(data, ensure_ascii=False))
            self.assertEqual(49, GRADER.apply_manifest(root, path)['correct'])


if __name__ == '__main__':
    unittest.main()
