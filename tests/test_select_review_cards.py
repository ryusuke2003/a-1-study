from __future__ import annotations

from datetime import date, datetime
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from zoneinfo import ZoneInfo


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "a1-adaptive-review"
    / "scripts"
    / "select_review_cards.py"
)
SPEC = importlib.util.spec_from_file_location("select_review_cards", SCRIPT)
assert SPEC and SPEC.loader
SELECTOR = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = SELECTOR
SPEC.loader.exec_module(SELECTOR)


def card(number: int, due: str, result: str, domain: str = "テスト"):
    last_reviewed = None if result == "unreviewed" else date(2026, 9, 1)
    return SELECTOR.Card(
        card_id=f"A1-{number:04d}",
        domain=domain,
        point=f"カード{number}の要点",
        source="[テスト](../学んだこと/テスト.md)",
        last_reviewed=last_reviewed,
        next_review=date.fromisoformat(due),
        stage=0 if result != "correct" else 1,
        last_result=result,
    )


class SelectReviewCardsTest(unittest.TestCase):
    def test_twenty_questions_use_seventy_twenty_ten_allocation(self) -> None:
        cards = []
        cards.extend(card(number, "2026-09-05", "correct") for number in range(1, 31))
        cards.extend(card(number, "2026-09-07", "incorrect") for number in range(31, 41))
        cards.extend(card(number, "2026-09-08", "unreviewed") for number in range(41, 51))

        result = SELECTOR.select_cards(cards, 20, date(2026, 9, 6))

        self.assertEqual(20, result["selected_count"])
        self.assertEqual({"deadline": 14, "weak": 4, "new_or_upcoming": 2}, result["quotas"])
        self.assertEqual(result["quotas"], result["actual_buckets"])
        ids = [entry["card_id"] for entry in result["cards"]]
        self.assertEqual(len(ids), len(set(ids)))

    def test_allocation_scales_to_fifty_questions(self) -> None:
        cards = []
        cards.extend(card(number, "2026-09-05", "correct") for number in range(1, 71))
        cards.extend(card(number, "2026-09-07", "unknown") for number in range(71, 91))
        cards.extend(card(number, "2026-09-08", "unreviewed") for number in range(91, 101))

        result = SELECTOR.select_cards(cards, 50, date(2026, 9, 6))

        self.assertEqual({"deadline": 35, "weak": 10, "new_or_upcoming": 5}, result["quotas"])
        self.assertEqual(result["quotas"], result["actual_buckets"])

    def test_overdue_weak_card_is_not_selected_twice(self) -> None:
        cards = [
            card(1, "2026-09-01", "incorrect"),
            card(2, "2026-09-02", "correct"),
            card(3, "2026-09-07", "unknown"),
            card(4, "2026-09-08", "unreviewed"),
        ]

        result = SELECTOR.select_cards(cards, 4, date(2026, 9, 6))

        ids = [entry["card_id"] for entry in result["cards"]]
        self.assertEqual(4, len(ids))
        self.assertEqual(4, len(set(ids)))

    def test_request_larger_than_card_count_returns_each_card_once(self) -> None:
        cards = [card(1, "2026-09-05", "correct"), card(2, "2026-09-07", "unknown")]

        result = SELECTOR.select_cards(cards, 30, date(2026, 9, 6))

        self.assertEqual(30, result["requested_count"])
        self.assertEqual(2, result["selected_count"])
        self.assertEqual(2, len({entry["card_id"] for entry in result["cards"]}))

    def test_missing_bucket_candidates_are_backfilled(self) -> None:
        cards = [card(number, "2026-09-05", "correct") for number in range(1, 10)]
        cards.append(card(10, "2026-09-07", "unreviewed"))

        result = SELECTOR.select_cards(cards, 10, date(2026, 9, 6))

        self.assertEqual(10, result["selected_count"])
        self.assertEqual(7, result["actual_buckets"]["deadline"])
        self.assertEqual(1, result["actual_buckets"]["new_or_upcoming"])
        self.assertEqual(2, result["actual_buckets"]["backfill"])

    def test_learning_day_changes_at_five_in_jst(self) -> None:
        timezone = ZoneInfo("Asia/Tokyo")
        before = datetime(2026, 9, 7, 4, 59, tzinfo=timezone)
        after = datetime(2026, 9, 7, 5, 0, tzinfo=timezone)

        self.assertEqual(date(2026, 9, 6), SELECTOR.logical_today(before))
        self.assertEqual(date(2026, 9, 7), SELECTOR.logical_today(after))

    def test_loader_rejects_unknown_result(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cards.md"
            path.write_text(
                "| Card ID | 分野 | 要点 | Source | Last Reviewed | Next Review | Stage | Last Result |\n"
                "|---|---|---|---|---|---|---:|---|\n"
                "| A1-0001 | テスト | 要点 | [資料](note.md) | - | 2026-09-07 | 0 | new |\n"
            )

            with self.assertRaisesRegex(SELECTOR.SelectionError, "未対応のLast Result"):
                SELECTOR.load_cards(path)


if __name__ == "__main__":
    unittest.main()
