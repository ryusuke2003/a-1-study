"""Keep the entrypoint light and task references measurable."""
from __future__ import annotations
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / 'skills/a1-adaptive-review'


class SkillContractTest(unittest.TestCase):
    def test_entrypoint_budget_and_no_detailed_grading_section(self) -> None:
        data = (SKILL / 'SKILL.md').read_bytes()
        self.assertLessEqual(len(data), 3000)
        text = data.decode('utf-8')
        self.assertNotIn('70%', text)
        self.assertNotIn('## \u63a1\u70b9\u3068\u5fa9\u7fd2\u671f\u9650', text)
        self.assertIn('select_review_cards.py', text)

    def test_all_relative_instruction_links_resolve(self) -> None:
        for path in SKILL.rglob('*.md'):
            for link in re.findall(r'\[[^]]*\]\(([^)]+)\)', path.read_text(encoding='utf-8')):
                if '://' in link or '<' in link:
                    continue
                target = link.split('#', 1)[0]
                if target:
                    self.assertTrue((path.parent / target).is_file(), f'{path}: {link}')

    def test_metric_auto_detects_routed_and_legacy_layouts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            baseline = Path(tmp)
            shutil.copytree(SKILL, baseline / 'skills/a1-adaptive-review')
            command = [sys.executable, str(ROOT / 'scripts/measure_skill_context.py'),
                       '--root', str(ROOT), '--baseline-dir', str(baseline)]
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(0, result.returncode, result.stderr)
            data = json.loads(result.stdout)
            self.assertEqual('routed', data['baseline_layout'])
            for task in data['tasks'].values():
                self.assertEqual(task['before'], task['after'])
            (baseline / 'skills/a1-adaptive-review/SKILL.md').write_text('# Legacy\n')
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual('legacy', json.loads(result.stdout)['baseline_layout'])


if __name__ == '__main__':
    unittest.main()
