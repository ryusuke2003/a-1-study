from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "a1-adaptive-review"
    / "scripts"
    / "grade_review_session.py"
)
SPEC = importlib.util.spec_from_file_location("grade_review_session", SCRIPT)
assert SPEC and SPEC.loader
GRADER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(GRADER)


def question(number: int, card_id: str, selected: str = "A") -> str:
    choices = {
        "A": f"{card_id}の正しい説明",
        "B": f"{card_id}と異なる処理を行う用語",
        "C": f"{card_id}と適用層が異なる用語",
        "D": f"{card_id}と目的が異なる用語",
        "E": "わかりません",
    }
    options = "\n".join(
        f"- [{'x' if choice == selected else ' '}] {choice}. {text}"
        for choice, text in choices.items()
    )
    return f"""### Q{number}

- Card ID: {card_id}
- Stage: 0
- Source: [テストノート](../../学んだこと/テスト.md)

### 問題

{card_id}について最も適切な説明はどれか。

{options}
"""


def explanation(choice: str) -> str:
    return (
        f"{choice}はテスト対象における固有の意味と役割を持つ用語です。"
        "利用場面と処理対象を比較すると、設問で問われた概念との違いを具体的に判断できます。"
    )


class RepositoryFixture:
    def __init__(self, root: Path, count: int, selected: str = "A") -> None:
        self.root = root
        (root / "学習記録/復習問題").mkdir(parents=True)
        (root / "学習記録/間違った問題").mkdir(parents=True)
        (root / "復習カード").mkdir(parents=True)
        (root / "学んだこと").mkdir(parents=True)
        scripts = root / "skills/a1-adaptive-review/scripts"
        scripts.mkdir(parents=True)
        (scripts / "verify_review_sessions.py").write_text(
            "import sys\nprint('検査成功')\nsys.exit(0)\n"
        )
        (root / "学んだこと/テスト.md").write_text(
            "# テストノート\n\n各カードの定義、役割、利用場面、類似概念との差を説明する資料です。\n"
        )
        rows = [
            "# 復習カード一覧",
            "",
            "| Card ID | 分野 | 要点 | Source | Last Reviewed | Next Review | Stage | Last Result |",
            "|---|---|---|---|---|---|---:|---|",
        ]
        blocks = []
        for number in range(1, count + 1):
            card_id = f"A1-{number:04d}"
            rows.append(
                f"| {card_id} | テスト | {card_id}の要点 | [テスト](../学んだこと/テスト.md) | - | 2026-09-02 | 0 | new |"
            )
            blocks.append(question(number, card_id, selected))
        (root / "復習カード/カード一覧.md").write_text("\n".join(rows) + "\n")
        self.session = root / "学習記録/復習問題/2026-09-02.md"
        self.session.write_text(
            "# 2026-09-02 復習問題\n\n"
            "## Session 1\n\n"
            "- Created: 2026-09-02\n"
            "- Status: awaiting_answers\n"
            f"- Question Count: {count}\n\n"
            + "\n".join(blocks)
        )


class GradeReviewSessionTest(unittest.TestCase):
    def test_prepare_includes_card_and_note_context(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            RepositoryFixture(root, 1, selected="E")
            draft = GRADER.make_draft(
                root, "学習記録/復習問題/2026-09-02.md", 1, "2026-09-02"
            )
            entry = draft["questions"][0]
            self.assertEqual("unknown", entry["result"])
            self.assertEqual("A1-0001の要点", entry["card_point"])
            self.assertIn("定義、役割、利用場面", entry["source_excerpt"])
            self.assertEqual(set("ABCD"), set(entry["explanations"]))

    def test_apply_thirty_questions_in_one_atomic_run(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = RepositoryFixture(root, 30)
            manifest = GRADER.make_draft(
                root, "学習記録/復習問題/2026-09-02.md", 1, "2026-09-02"
            )
            for entry in manifest["questions"]:
                entry["correct_answer"] = "A"
                entry["result"] = "correct"
            manifest_path = root / "grading.json"
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False))

            results = GRADER.apply_manifest(root, manifest_path)

            self.assertEqual(30, results["correct"])
            text = fixture.session.read_text()
            self.assertEqual(30, text.count("### 採点"))
            self.assertIn("- Status: graded", text)
            self.assertIn(
                "Grading Audit: questions=30, graded=30, correct=30, incorrect=0, unknown=0",
                text,
            )
            cards = (root / "復習カード/カード一覧.md").read_text()
            self.assertEqual(30, cards.count("| 2026-09-02 | 2026-09-05 | 1 | correct |"))
            self.assertFalse((root / "学習記録/間違った問題/2026-09-02.md").exists())

    def test_wrong_answer_requires_and_writes_all_choice_explanations(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            RepositoryFixture(root, 1, selected="B")
            manifest = GRADER.make_draft(
                root, "学習記録/復習問題/2026-09-02.md", 1, "2026-09-02"
            )
            entry = manifest["questions"][0]
            entry["correct_answer"] = "A"
            entry["result"] = "incorrect"
            entry["explanations"] = {choice: explanation(choice) for choice in "ABCD"}
            manifest_path = root / "grading.json"
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False))

            GRADER.apply_manifest(root, manifest_path)

            wrong = (root / "学習記録/間違った問題/2026-09-02.md").read_text()
            self.assertIn("### 問題", wrong)
            self.assertIn("### 模範解答", wrong)
            for choice in "ABCD":
                self.assertIn(f"- {choice}:", wrong)
                self.assertIn(f"→ {explanation(choice)}", wrong)

    def test_draft_explanations_cannot_be_applied(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            RepositoryFixture(root, 1, selected="B")
            manifest = GRADER.make_draft(
                root, "学習記録/復習問題/2026-09-02.md", 1, "2026-09-02"
            )
            manifest["questions"][0]["correct_answer"] = "A"
            manifest["questions"][0]["result"] = "incorrect"
            manifest_path = root / "grading.json"
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False))

            with self.assertRaisesRegex(GRADER.GradingError, "下書きを完成"):
                GRADER.apply_manifest(root, manifest_path, dry_run=True)

    def test_verifier_failure_rolls_back_all_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = RepositoryFixture(root, 1, selected="B")
            cards_path = root / "復習カード/カード一覧.md"
            original_session = fixture.session.read_text()
            original_cards = cards_path.read_text()
            verifier = root / "skills/a1-adaptive-review/scripts/verify_review_sessions.py"
            verifier.write_text("import sys\nprint('検査失敗')\nsys.exit(1)\n")
            manifest = GRADER.make_draft(
                root, "学習記録/復習問題/2026-09-02.md", 1, "2026-09-02"
            )
            entry = manifest["questions"][0]
            entry["correct_answer"] = "A"
            entry["result"] = "incorrect"
            entry["explanations"] = {choice: explanation(choice) for choice in "ABCD"}
            manifest_path = root / "grading.json"
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False))

            with self.assertRaisesRegex(GRADER.GradingError, "自動検査に失敗"):
                GRADER.apply_manifest(root, manifest_path)

            self.assertEqual(original_session, fixture.session.read_text())
            self.assertEqual(original_cards, cards_path.read_text())
            self.assertFalse((root / "学習記録/間違った問題/2026-09-02.md").exists())

    def test_cleanup_removes_moved_comment_without_touching_visible_record(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            wrong_dir = root / "学習記録/間違った問題"
            wrong_dir.mkdir(parents=True)
            path = wrong_dir / "2026-09-01.md"
            path.write_text(
                "# 記録\n\n## Session 1 / Q1: A1-0001\n本文\n\n"
                "<!-- 採点日2026-09-02へ移動済み\n## Session 1 / Q2: A1-0002\n旧本文\n-->\n"
            )
            self.assertEqual(1, GRADER.cleanup_moved_comments(root))
            cleaned = path.read_text()
            self.assertIn("Q1", cleaned)
            self.assertNotIn("移動済み", cleaned)
            self.assertNotIn("Q2", cleaned)


if __name__ == "__main__":
    unittest.main()
