"""Keep past-exam filing copies identical to their source-note blocks."""
from __future__ import annotations

from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
START_RE = re.compile(r"<!-- past-exam-sync: (?P<key>.+):start -->")
END_RE = re.compile(r"<!-- past-exam-sync: (?P<key>.+):end -->")
BLOCK_RE = re.compile(
    r"<!-- past-exam-sync: (?P<key>.+):start -->\n"
    r"(?P<body>.*?)\n"
    r"<!-- past-exam-sync: (?P=key):end -->",
    re.DOTALL,
)
QUESTION_RE = re.compile(r"#午前I-問(?P<number>\d+)$")


class PastExamSyncTest(unittest.TestCase):
    def _collect(self, directory: Path) -> dict[str, tuple[str, str]]:
        blocks: dict[str, tuple[str, str]] = {}
        for path in sorted(directory.rglob("*.md")):
            text = path.read_text(encoding="utf-8")
            starts = START_RE.findall(text)
            ends = END_RE.findall(text)
            self.assertEqual(starts, ends, f"unmatched sync marker: {path}")

            for match in BLOCK_RE.finditer(text):
                key = match.group("key")
                relative = path.relative_to(ROOT).as_posix()
                self.assertNotIn(key, blocks, f"duplicate sync key: {key}")
                blocks[key] = (match.group("body"), relative)
        return blocks

    def test_past_exam_blocks_match_source_notes(self) -> None:
        source = self._collect(ROOT / "学んだこと")
        past_exam = self._collect(ROOT / "過去問")

        self.assertEqual(set(source), set(past_exam))
        for key, (source_body, _) in source.items():
            past_body, past_path = past_exam[key]
            target_path = key.split("#", 1)[0]
            self.assertTrue(target_path.startswith("過去問/"), key)
            self.assertEqual(target_path, past_path, key)
            self.assertEqual(source_body, past_body, key)

    def test_past_exam_blocks_are_sorted_by_question_number(self) -> None:
        for path in sorted((ROOT / "過去問").rglob("*.md")):
            text = path.read_text(encoding="utf-8")
            numbers = []
            for key in START_RE.findall(text):
                match = QUESTION_RE.search(key)
                if match:
                    numbers.append(int(match.group("number")))
            self.assertEqual(numbers, sorted(numbers), f"question order: {path}")


if __name__ == "__main__":
    unittest.main()
