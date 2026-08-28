import subprocess
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "a1_review.py"


class A1復習スクリプトのテスト(unittest.TestCase):
    def 実行(self, root, *args):
        result = subprocess.run(["python3", str(SCRIPT), "--root", str(root), *args], text=True, capture_output=True)
        if result.returncode:
            self.fail(result.stderr)
        return result

    def test_カード作成で初回復習日と一覧を作る(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.実行(root, "init")
            self.実行(root, "add-card", "--domain", "ネットワーク", "--title", "TCP", "--point", "接続確立の順序", "--source", "../../学んだこと/ネットワーク.md")
            card = next((root / "復習カード" / "カード本文").glob("*.md")).read_text(encoding="utf-8")
            self.assertIn("- Stage: 0", card)
            self.assertIn("- Last Result: unreviewed", card)
            self.assertIn("A1-0001", (root / "復習カード" / "カード一覧.md").read_text(encoding="utf-8"))

    def test_正答で段階と次回復習日を更新する(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.実行(root, "init")
            self.実行(root, "add-card", "--domain", "ネットワーク", "--title", "TCP", "--point", "接続確立の順序", "--source", "../../学んだこと/ネットワーク.md")
            session = root / "学習記録/復習問題/2026-08-28.md"
            key = root / "学習記録/復習問題/解答/2026-08-28.md"
            session.write_text("""# 復習問題\n\n- Status: awaiting_answers\n\n### Q1\n\n- Card ID: A1-0001\n\n### 問題\n\n- [x] A. 正解\n- [ ] B. 誤答\n- [ ] C. 誤答\n- [ ] D. 誤答\n- [ ] E. わかりません\n""", encoding="utf-8")
            key.write_text("""# 解答\n\n### Q1\n\n- Correct: A\n- Explanation: Aが正解です。\n""", encoding="utf-8")
            self.実行(root, "grade", "--session", "学習記録/復習問題/2026-08-28.md", "--key", "学習記録/復習問題/解答/2026-08-28.md")
            card = next((root / "復習カード" / "カード本文").glob("*.md")).read_text(encoding="utf-8")
            self.assertIn("- Stage: 1", card)
            self.assertIn("- Last Result: correct", card)
            self.assertIn("- Status: graded", session.read_text(encoding="utf-8"))

    def test_わかりませんで翌日復習へ戻す(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.実行(root, "init")
            self.実行(root, "add-card", "--domain", "ネットワーク", "--title", "TCP", "--point", "接続確立の順序", "--source", "../../学んだこと/ネットワーク.md")
            session = root / "学習記録/復習問題/2026-08-28.md"
            key = root / "学習記録/復習問題/解答/2026-08-28.md"
            session.write_text("""# 復習問題\n\n- Status: awaiting_answers\n\n### Q1\n\n- Card ID: A1-0001\n\n### 問題\n\n- [ ] A. 正解\n- [ ] B. 誤答\n- [ ] C. 誤答\n- [ ] D. 誤答\n- [x] E. わかりません\n""", encoding="utf-8")
            key.write_text("""# 解答\n\n### Q1\n\n- Correct: A\n- Explanation: Aが正解です。\n""", encoding="utf-8")
            self.実行(root, "grade", "--session", "学習記録/復習問題/2026-08-28.md", "--key", "学習記録/復習問題/解答/2026-08-28.md")
            card = next((root / "復習カード" / "カード本文").glob("*.md")).read_text(encoding="utf-8")
            self.assertIn("- Stage: 0", card)
            self.assertIn("- Last Result: unknown", card)
