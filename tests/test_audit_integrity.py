"""The independent audit must detect drift in copies and hand-edited card state."""
from __future__ import annotations
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from test_grade_review_session import GRADER as G, RepositoryFixture, explanation

SCRIPT=Path(G.__file__).with_name('verify_review_sessions.py')
MISTAKES=Path('\u5b66\u7fd2\u8a18\u9332/\u9593\u9055\u3048\u305f\u554f\u984c')
CARDS=Path('\u5fa9\u7fd2\u30ab\u30fc\u30c9/\u30ab\u30fc\u30c9\u4e00\u89a7.md')


class AuditIntegrityTest(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name)
        fixture=RepositoryFixture(self.root,1,'B')
        self.session=fixture.session
        self.session.write_text(self.session.read_text().replace('[x]','[X]'))
        data=G.make_draft(self.root,self.session.relative_to(self.root).as_posix(),1,'2026-09-02')
        data['questions'][0].update(correct_answer='A',result='incorrect',explanations={c:explanation(c) for c in 'ABCD'})
        manifest=self.root/'m.json'
        manifest.write_text(json.dumps(data))
        G.apply_manifest(self.root,manifest)
        self.mistakes=self.root/MISTAKES/'2026-09-02.md'
        self.cards=self.root/CARDS

    def audit(self):
        return subprocess.run([sys.executable,str(SCRIPT),'--root',str(self.root)],text=True,capture_output=True)

    def test_valid_records_and_uppercase_checkbox_pass_without_writes(self):
        before={p:p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        process=self.audit()
        self.assertEqual(0,process.returncode,process.stdout+process.stderr)
        self.assertEqual(before,{p:p.read_bytes() for p in self.root.rglob('*') if p.is_file()})

    def test_wrong_copied_problem_is_detected(self):
        text=self.mistakes.read_text()
        text=text.replace('A1-0001\u306b\u3064\u3044\u3066', 'A1-9999\u306b\u3064\u3044\u3066')
        self.mistakes.write_text(text)
        p=self.audit()
        self.assertNotEqual(0,p.returncode)
        self.assertIn('copied problem',p.stdout)

    def test_wrong_model_answer_is_detected(self):
        text=self.mistakes.read_text()
        label='### \u6a21\u7bc4\u89e3\u7b54\n\n'
        text=text.replace(label+'A.',label+'B.')
        self.mistakes.write_text(text)
        p=self.audit()
        self.assertNotEqual(0,p.returncode)
        self.assertIn('model answer',p.stdout)

    def test_wrong_your_answer_is_detected(self):
        self.mistakes.write_text(self.mistakes.read_text().replace('- Your Answer: B.', '- Your Answer: C.'))
        p=self.audit()
        self.assertNotEqual(0,p.returncode)
        self.assertIn('Your Answer',p.stdout)

    def test_correct_answer_must_match_original_option(self):
        self.mistakes.write_text(self.mistakes.read_text().replace('- Correct Answer: A.', '- Correct Answer: D.'))
        p=self.audit()
        self.assertNotEqual(0,p.returncode)
        self.assertIn('Correct Answer',p.stdout)

    def test_duplicate_card_id_is_detected(self):
        self.cards.write_text(self.cards.read_text()+self.cards.read_text().splitlines()[-1]+'\n')
        p=self.audit()
        self.assertNotEqual(0,p.returncode)
        self.assertIn('Duplicate Card ID',p.stdout)

    def test_invalid_card_date_is_detected_without_traceback(self):
        self.cards.write_text(self.cards.read_text().replace('2026-09-03','2026-99-99'))
        p=self.audit()
        self.assertNotEqual(0,p.returncode)
        self.assertNotIn('Traceback',p.stderr)

    def test_invalid_graded_date_is_detected_without_traceback(self):
        self.session.write_text(self.session.read_text().replace('- Graded: 2026-09-02','- Graded: 2026-99-99'))
        p=self.audit()
        self.assertNotEqual(0,p.returncode)
        self.assertNotIn('Traceback',p.stderr)

    def test_unknown_session_status_is_not_silently_skipped(self):
        self.session.write_text(self.session.read_text().replace('- Status: graded','- Status: grdaed'))
        p=self.audit()
        self.assertNotEqual(0,p.returncode)
        self.assertIn('Status',p.stdout)

    def test_missing_session_directory_is_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=subprocess.run([sys.executable,str(SCRIPT),'--root',tmp],text=True,capture_output=True)
            self.assertNotEqual(0,p.returncode)
            self.assertNotIn('Traceback',p.stderr)


if __name__=='__main__':
    unittest.main()
