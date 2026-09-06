"""Regressions for grading integrity, isolation, source access and resource cleanup."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import quote

from test_grade_review_session import GRADER as G, RepositoryFixture, explanation, question

SCRIPT = Path(G.__file__)
CARDS = Path('\u5fa9\u7fd2\u30ab\u30fc\u30c9/\u30ab\u30fc\u30c9\u4e00\u89a7.md')
NOTES = Path('\u5b66\u3093\u3060\u3053\u3068')
MISTAKES = Path('\u5b66\u7fd2\u8a18\u9332/\u9593\u9055\u3048\u305f\u554f\u984c')


class GradingSafetyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.fixture = RepositoryFixture(self.root, 1)
        self.file = self.fixture.session
        self.relative = self.file.relative_to(self.root).as_posix()
        self.cards = self.root / CARDS

    def draft(self, day='2026-09-02', file=None):
        relative = (file or self.file).relative_to(self.root).as_posix()
        data = G.make_draft(self.root, relative, 1, day)
        for e in data['questions']:
            e.update(correct_answer='A', result=G.expected_result(None if e['selected']=='unselected' else e['selected'], 'A'))
            e['explanations'] = {c: explanation(c) for c in 'ABCD'}
        return data

    def apply(self, data, dry=True):
        path = self.root / 'manifest.json'
        path.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
        return G.apply_manifest(self.root, path, dry_run=dry)

    def md_snapshot(self):
        return {p: p.read_bytes() for p in self.root.rglob('*.md')}

    def test_same_heading_from_different_source_dates_is_not_a_duplicate(self):
        self.file.write_text(self.file.read_text().replace('[x] A.', '[ ] A.').replace('[ ] B.', '[x] B.'))
        other = self.file.with_name('2026-09-03.md')
        other.write_text(self.file.read_text().replace('2026-09-02', '2026-09-03'))
        verifier = SCRIPT.with_name('verify_review_sessions.py')
        shutil.copy2(verifier, self.root / 'skills/a1-adaptive-review/scripts' / verifier.name)
        for file in (self.file, other):
            self.apply(self.draft('2026-09-06', file), dry=False)
        text = (self.root / MISTAKES / '2026-09-06.md').read_text()
        self.assertEqual(2, text.count('## Session 1 / Q1: A1-0001'))
        self.assertIn('2026-09-02.md#session-1', text)
        self.assertIn('2026-09-03.md#session-1', text)

    def test_duplicate_from_same_source_is_still_rejected(self):
        data = self.draft()
        _, _, block = G.find_session(self.file.read_text(), 1)
        q = G.parse_questions(block)[0]
        item = dict(q, entry=data['questions'][0], result='incorrect')
        existing = G.mistake_record(item, self.relative, 1)
        with self.assertRaises(G.GradingError):
            G.update_mistakes(existing, [item], self.relative, 1, '2026-09-02')

    def test_stale_stage_rejected_even_in_legacy_manifest(self):
        data = self.draft()
        data.pop('card_sha256', None)
        self.cards.write_text(self.cards.read_text().replace('| 0 | new |', '| 2 | correct |'))
        before = self.md_snapshot()
        with self.assertRaises(G.GradingError):
            self.apply(data, dry=False)
        self.assertEqual(before, self.md_snapshot())
        self.assertFalse((self.root / G.LOCK_NAME).exists())

    def test_prepare_rejects_stale_stage(self):
        self.cards.write_text(self.cards.read_text().replace('| 0 | new |', '| 2 | correct |'))
        with self.assertRaises(G.GradingError):
            self.draft()

    def test_target_card_content_change_invalidates_draft(self):
        data = self.draft()
        self.cards.write_text(self.cards.read_text().replace('A1-0001\u306e\u8981\u70b9', 'Updated point'))
        with self.assertRaises(G.GradingError):
            self.apply(data)

    def test_unrelated_card_addition_does_not_invalidate_draft(self):
        data = self.draft()
        row = '| A1-0002 | D | Unrelated | [N](n.md) | - | 2026-09-03 | 0 | unreviewed |\n'
        self.cards.write_text(self.cards.read_text()+row)
        self.assertEqual(1, self.apply(data)['correct'])

    def test_fractional_boolean_and_invalid_question_numbers_rejected(self):
        data = self.draft()
        for value in (1.9, 1.0, True, False, None, '1.0', 0, -1):
            with self.subTest(value=value):
                data['questions'][0]['q'] = value
                with self.assertRaises(G.GradingError):
                    self.apply(data)

    def test_legacy_digit_strings_remain_supported(self):
        data = self.draft()
        data['questions'][0]['q'] = '1'
        data['session'] = '1'
        data.pop('card_sha256')
        data.pop('session_sha256')
        self.assertEqual(1, self.apply(data)['correct'])

    def test_malformed_manifest_shapes_fail_before_writes(self):
        before = self.md_snapshot()
        for data in (None, [], 2, {}, {'session': True}, {'session': 1.9}):
            with self.subTest(data=data), self.assertRaises(G.GradingError):
                self.apply(data, dry=False)
        data = self.draft()
        for entries in (None, {}, [None], [1], ['Q1']):
            data['questions'] = entries
            with self.subTest(entries=entries), self.assertRaises(G.GradingError):
                self.apply(data, dry=False)
        self.assertEqual(before, self.md_snapshot())

    def test_duplicate_json_keys_fail_without_traceback(self):
        path = self.root/'duplicate.json'
        for text in ('{"session":1,"session":2}', '{"questions":[{"q":1,"q":2}]}'):
            path.write_text(text)
            process = subprocess.run([sys.executable, str(SCRIPT), '--root', str(self.root), 'apply', str(path)], text=True, capture_output=True)
            self.assertNotEqual(0, process.returncode)
            self.assertNotIn('Traceback', process.stderr)
            self.assertIn('Duplicate JSON key', process.stderr)

    def test_invalid_or_backdated_grading_date_is_rejected(self):
        for day in ('20260902', '2026-99-99', '2026-09-01', None):
            with self.subTest(day=day), self.assertRaises(G.GradingError):
                self.draft(day)
        data = self.draft('2026-09-03')
        self.cards.write_text(self.cards.read_text().replace('| - |', '| 2026-09-04 |'))
        with self.assertRaises(G.GradingError):
            self.apply(data)

    def test_unknown_card_and_duplicate_table_rows_fail(self):
        self.file.write_text(self.file.read_text().replace('A1-0001','A1-9999'))
        with self.assertRaises(G.GradingError):
            self.draft()
        self.file.write_text(self.file.read_text().replace('A1-9999','A1-0001'))
        row = self.cards.read_text().splitlines()[-1]
        self.cards.write_text(self.cards.read_text()+row+'\n')
        with self.assertRaises(G.GradingError):
            self.draft()

    def test_same_card_twice_in_session_fails(self):
        self.file.write_text(self.file.read_text().replace('Question Count: 1','Question Count: 2')+'\n'+question(2,'A1-0001'))
        with self.assertRaises(G.GradingError):
            self.draft()

    def test_advisory_identity_and_selection_must_match(self):
        for key, value in [('card_id','A1-9999'),('selected','B')]:
            data = self.draft()
            data['questions'][0][key] = value
            with self.subTest(key=key), self.assertRaises(G.GradingError):
                self.apply(data)

    def test_source_cannot_escape_notes_or_use_external_url(self):
        outside = self.root/'outside.md'
        outside.write_text('DUMMY not a study note')
        for source in ('../../outside.md', 'https://example.com/n.md', 'file:///tmp/n.md', '../../%6futside.md'):
            with self.subTest(source=source), self.assertRaises(G.GradingError):
                G.source_excerpt(self.file, source, [], root=self.root)

    def test_missing_source_and_symlink_escape_fail(self):
        with self.assertRaises(G.GradingError):
            G.source_excerpt(self.file, '../../'+NOTES.as_posix()+'/missing.md', [], root=self.root)
        external = self.root/'outside.md'
        external.write_text('DUMMY')
        link = self.root/NOTES/'link.md'
        link.symlink_to(external)
        with self.assertRaises(G.GradingError):
            G.source_excerpt(self.file, '../../'+NOTES.as_posix()+'/link.md', [], root=self.root)

    def test_encoded_source_link_is_supported(self):
        source = '../../'+NOTES.as_posix()+'/\u30c6\u30b9\u30c8.md'
        plain = G.source_excerpt(self.file, source, [], root=self.root)
        self.assertEqual(plain, G.source_excerpt(self.file, quote(source)+'#heading', [], root=self.root))

    def test_note_is_read_once_per_prepare_not_once_per_question(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = RepositoryFixture(root, 20)
            actual = Path.read_text
            reads = []
            def read(path, *args, **kwargs):
                if path.is_relative_to(root/NOTES):
                    reads.append(path)
                return actual(path, *args, **kwargs)
            with patch.object(Path, 'read_text', read):
                G.make_draft(root, fixture.session.relative_to(root).as_posix(),1,'2026-09-02')
            self.assertEqual(1,len(reads))
            note = reads[0]
            note.write_text('# Updated\n\nNEW_CONTENT\n')
            new = G.make_draft(root, fixture.session.relative_to(root).as_posix(),1,'2026-09-02')
            self.assertIn('NEW_CONTENT', new['questions'][0]['source_excerpt'])

    def test_prepare_rejects_session_outside_repository(self):
        with tempfile.TemporaryDirectory() as tmp:
            outside = Path(tmp)/'2026-09-02.md'
            outside.write_text(self.file.read_text())
            with self.assertRaises(G.GradingError):
                G.make_draft(self.root, str(outside), 1, '2026-09-02')

    def test_cli_draft_cannot_overwrite_repository_file(self):
        before = self.md_snapshot()
        p = subprocess.run([sys.executable, str(SCRIPT),'--root',str(self.root), 'prepare',self.relative,
                            '--graded-on','2026-09-02','--output',str(self.file)],capture_output=True,text=True)
        self.assertNotEqual(0,p.returncode)
        self.assertEqual(before,self.md_snapshot())

    def test_draft_output_is_private(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'draft.json'
            process = subprocess.run([sys.executable,str(SCRIPT),'--root',str(self.root),'prepare',self.relative,
                                      '--graded-on','2026-09-02','--output',str(path)],capture_output=True,text=True)
            self.assertEqual(0,process.returncode,process.stderr)
            self.assertEqual(0o600,path.stat().st_mode & 0o777)

    def test_non_string_or_multiline_explanation_is_rejected(self):
        self.file.write_text(self.file.read_text().replace('[x] A.', '[ ] A.').replace('[ ] B.', '[x] B.'))
        for value in (None, ['unexpected long explanation'], 'one line explanation\n### injected heading'):
            data = self.draft()
            data['questions'][0]['explanations']['A'] = value
            with self.subTest(value=value), self.assertRaises(G.GradingError):
                self.apply(data)

    def test_lock_prevents_second_apply_and_cleanup(self):
        data = self.draft()
        before = self.md_snapshot()
        with G.grading_lock(self.root):
            with self.assertRaises(G.GradingError):
                self.apply(data, dry=False)
            with self.assertRaises(G.GradingError):
                G.cleanup_moved_comments(self.root)
            self.assertEqual(1,self.apply(data,dry=True)['correct'])
        self.assertEqual(before,self.md_snapshot())
        self.assertFalse((self.root/G.LOCK_NAME).exists())

    def test_another_process_cannot_apply_while_lock_is_held(self):
        data = self.draft()
        path = self.root/'manifest.json'
        path.write_text(json.dumps(data))
        before = self.md_snapshot()
        with G.grading_lock(self.root):
            process = subprocess.run([sys.executable,str(SCRIPT),'--root',str(self.root),
                                      'apply',str(path)],text=True,capture_output=True)
            self.assertNotEqual(0,process.returncode)
            self.assertIn('locked',process.stderr)
        self.assertEqual(before,self.md_snapshot())

    def test_unbounded_stage_fails_without_computing_huge_power(self):
        with self.assertRaises(G.GradingError):
            G.next_review('2026-09-02',10**12,'correct')

    def test_lock_is_released_on_error(self):
        with self.assertRaises(RuntimeError):
            with G.grading_lock(self.root):
                raise RuntimeError('test')
        self.assertFalse((self.root/G.LOCK_NAME).exists())

    def test_atomic_write_failure_leaves_no_temp_file(self):
        target = self.root/'target.txt'
        target.write_text('old')
        before = {p.name for p in self.root.iterdir()}
        with patch.object(G.os,'replace',side_effect=OSError('test failure')):
            with self.assertRaises(OSError):
                G.atomic_write(target,'new')
        self.assertEqual('old',target.read_text())
        self.assertEqual(before,{p.name for p in self.root.iterdir()})

    def test_timeout_and_interrupt_rollback_exact_crlf_bytes(self):
        self.file.write_bytes(self.file.read_bytes().replace(b'\n',b'\r\n'))
        self.cards.write_bytes(self.cards.read_bytes().replace(b'\n',b'\r\n'))
        data = self.draft()
        before = self.md_snapshot()
        for exc in (subprocess.TimeoutExpired('verifier',30), KeyboardInterrupt()):
            with self.subTest(error=type(exc).__name__), patch.object(G.subprocess,'run',side_effect=exc):
                with self.assertRaises(type(exc)):
                    self.apply(data,dry=False)
            self.assertEqual(before,self.md_snapshot())
            self.assertFalse((self.root/G.LOCK_NAME).exists())


if __name__ == '__main__':
    unittest.main()
