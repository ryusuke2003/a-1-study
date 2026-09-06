"""Deterministic, read-only status reporting based on canonical Markdown."""
from __future__ import annotations

from datetime import date
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from test_select_review_cards import card, SELECTOR as S

SCRIPT = Path(S.__file__).with_name('study_status.py')
sys.path.insert(0, str(SCRIPT.parent))
SPEC = importlib.util.spec_from_file_location('study_status', SCRIPT)
STATUS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(STATUS)
ON = date(2026, 9, 6)


class StudyStatusTest(unittest.TestCase):
    def cards(self):
        return [card(1,'2026-09-05','correct','D1'), card(2,'2026-09-06','incorrect','D1'),
                card(3,'2026-09-09','correct','D2'), card(4,'2026-09-06','unreviewed','D2'),
                card(5,'2026-10-20','unreviewed','D2'), card(6,'2026-09-01','unknown','D3')]

    def test_disjoint_due_counts_and_overlapping_traits(self):
        report = STATUS.summarize(self.cards(),{'A1-0002':['day#session-1'],'A1-0006':['day#session-2']},ON)
        self.assertEqual(dict(total=6,overdue=2,due_today=2,future=2,ready_due=2,pending=2,unreviewed=2,weak=2),report['counts'])
        self.assertEqual(6,sum(d['total'] for d in report['domains']))

    def test_schedule_uses_stored_dates_without_rescheduling_overdue(self):
        r=STATUS.summarize(self.cards(),{'A1-0002':['day#session-1']},ON)
        self.assertEqual(7,len(r['schedule']))
        self.assertEqual(dict(on='2026-09-06',scheduled=2,pending=1,ready=1),r['schedule'][0])
        self.assertEqual(1,r['schedule'][3]['scheduled'])
        self.assertEqual(3,sum(d['scheduled'] for d in r['schedule']))

    def test_pending_card_is_counted_once_across_two_sessions(self):
        report=STATUS.summarize(self.cards(),{'A1-0002':['b#session-2','a#session-1']},ON)
        self.assertEqual(1,report['counts']['pending'])
        self.assertEqual(['a#session-1','b#session-2'],report['pending_sessions'])

    def test_empty_repository_and_no_question_generation(self):
        r=STATUS.summarize([],{},ON)
        self.assertEqual(0,r['counts']['total'])
        self.assertEqual([],r['domains'])
        self.assertNotIn('### Q',STATUS.markdown(r))
        self.assertNotIn('correct_answer',json.dumps(r))

    def test_order_is_independent_of_card_order(self):
        self.assertEqual(STATUS.summarize(self.cards(),{},ON),STATUS.summarize(self.cards()[::-1],{},ON))

    def test_unknown_pending_card_fails(self):
        with self.assertRaises(STATUS.SelectionError):
            STATUS.summarize(self.cards(),{'A1-9999':['day#session-1']},ON)

    def test_duplicate_card_fails(self):
        c=self.cards()[0]
        with self.assertRaises(STATUS.SelectionError):
            STATUS.summarize([c,c],{},ON)

    def test_invalid_schedule_size_fails(self):
        for days in (-1,0,367,True,1.5):
            with self.subTest(days=days),self.assertRaises(STATUS.SelectionError):
                STATUS.summarize([],{},ON,days)

    def test_cli_json_and_markdown_never_change_study_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            table=root/S.CARDS_PATH
            table.parent.mkdir(parents=True)
            table.write_text('| A1-0001 | D | Point | [N](n.md) | - | 2026-09-06 | 0 | unreviewed |\n')
            sessions=root/S.SESSIONS_PATH
            sessions.mkdir(parents=True)
            (sessions/'2026-09-06.md').write_text('## Session 1\n\n- Status: awaiting_answers\n- Question Count: 1\n\n### Q1\n\n- Card ID: A1-0001\n')
            before={p:p.read_bytes() for p in root.rglob('*') if p.is_file()}
            for format in ('json','markdown'):
                cmd=[sys.executable,str(SCRIPT),'--root',str(root),'--on','2026-09-06','--format',format]
                process=subprocess.run(cmd,text=True,capture_output=True)
                self.assertEqual(0,process.returncode,process.stderr)
                if format=='json':
                    report=json.loads(process.stdout)
                    self.assertEqual(1,report['counts']['pending'])
                    self.assertEqual(0,report['counts']['ready_due'])
                else:
                    self.assertIn('2026-09-06.md#session-1',process.stdout)
            self.assertEqual(before,{p:p.read_bytes() for p in root.rglob('*') if p.is_file()})

    def test_cli_missing_repository_fails_instead_of_reporting_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            process=subprocess.run([sys.executable,str(SCRIPT),'--root',tmp],text=True,capture_output=True)
            self.assertNotEqual(0,process.returncode)
            self.assertNotIn('Traceback',process.stderr)


if __name__=='__main__':
    unittest.main()
