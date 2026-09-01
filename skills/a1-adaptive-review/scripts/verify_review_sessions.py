#!/usr/bin/env python3
"""Verify graded A-1 review sessions without modifying study data."""
from __future__ import annotations

from collections import Counter, defaultdict
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SESSIONS = ROOT / "学習記録" / "復習問題"
WRONG = ROOT / "学習記録" / "間違った問題"
CARDS = ROOT / "復習カード" / "カード一覧.md"


def chunks(text: str, pattern: str) -> list[re.Match[str]]:
    return list(re.finditer(pattern, text, re.MULTILINE | re.DOTALL))


def field(text: str, name: str, pattern: str) -> str | None:
    match = re.search(rf"^- {re.escape(name)}: ({pattern})$", text, re.MULTILINE)
    return match.group(1) if match else None


def load_cards(errors: list[str]) -> dict[str, dict[str, str]]:
    cards: dict[str, dict[str, str]] = {}
    for line in CARDS.read_text().splitlines():
        if not re.match(r"^\| A1-\d+ \|", line):
            continue
        values = [value.strip() for value in line.strip().strip("|").split("|")]
        if len(values) != 8:
            errors.append(f"カード一覧: {values[0]} の列数が8ではありません")
            continue
        cards[values[0]] = {
            "last_reviewed": values[4],
            "next_review": values[5],
            "stage": values[6],
            "last_result": values[7],
        }
    return cards


def load_wrong_records() -> dict[tuple[str, int, int, str], list[str]]:
    records: dict[tuple[str, int, int, str], list[str]] = defaultdict(list)
    heading = r"^## Session (\d+) / Q(\d+): (A1-\d+)\n(.*?)(?=^## |\Z)"
    for path in sorted(WRONG.glob("*.md")):
        for match in chunks(path.read_text(), heading):
            session, qno, card_id, body = match.groups()
            source = re.search(
                r"^- Source Session: \[[^\]]+\]\(([^)#]+)(?:#[^)]+)?\)$",
                body,
                re.MULTILINE,
            )
            source_name = Path(source.group(1)).name if source else ""
            records[(source_name, int(session), int(qno), card_id)].append(body)
    return records


def main() -> int:
    errors: list[str] = []
    cards = load_cards(errors)
    wrong_records = load_wrong_records()
    reviews: list[dict[str, object]] = []
    checked = 0

    for path in sorted(SESSIONS.glob("*.md")):
        if path.name == "README.md":
            continue
        text = path.read_text()
        for session in chunks(text, r"^## Session (\d+)\n(.*?)(?=^## Session |\Z)"):
            number = int(session.group(1))
            body = session.group(2)
            status = field(body, "Status", r"[^\s]+")
            label = f"{path.name} Session {number}"
            if status == "completed":
                errors.append(f"{label}: 旧Status completedをgradedへ移行してください")
                continue
            if status != "graded":
                continue

            checked += 1
            graded_on = field(body, "Graded", r"\d{4}-\d{2}-\d{2}")
            declared = field(body, "Question Count", r"\d+")
            questions = chunks(body, r"^### Q(\d+)\n(.*?)(?=^### Q\d+|\Z)")
            if declared is None or int(declared) != len(questions):
                errors.append(f"{label}: Question Count が実際の設問数と一致しません")
            numbers = [int(question.group(1)) for question in questions]
            if numbers != list(range(1, len(questions) + 1)):
                errors.append(f"{label}: Q番号がQ1から連番ではありません")
            if graded_on is None:
                errors.append(f"{label}: Gradedがありません")

            session_results: Counter[str] = Counter()
            for question in questions:
                qno = int(question.group(1))
                block = question.group(2)
                qlabel = f"{label} Q{qno}"
                card_id = field(block, "Card ID", r"A1-\d+")
                stage_text = field(block, "Stage", r"\d+\+?")
                selected = re.findall(r"^- \[x\] ([A-E])\. ", block, re.MULTILINE)
                grades = chunks(block, r"^### 採点\n(.*?)(?=^### Q\d+|\Z)")
                if card_id is None:
                    errors.append(f"{qlabel}: Card IDがありません")
                    continue
                if stage_text is None:
                    errors.append(f"{qlabel}: Stageがありません")
                if len(selected) > 1:
                    errors.append(f"{qlabel}: 選択済みの選択肢が複数あります")
                if len(grades) != 1:
                    errors.append(f"{qlabel}: 採点欄がちょうど一つではありません")
                    continue

                grade = grades[0].group(1)
                result = re.search(r"^Result: (correct|incorrect|unknown)$", grade, re.MULTILINE)
                score = re.search(r"^Score: (100|0) / 100$", grade, re.MULTILINE)
                next_review = re.search(
                    r"^Next Review: (\d{4}-\d{2}-\d{2})$", grade, re.MULTILINE
                )
                if not result or not score or not next_review:
                    errors.append(f"{qlabel}: Score、ResultまたはNext Reviewが不正です")
                    continue

                result_value = result.group(1)
                session_results[result_value] += 1
                expected_score = "100" if result_value == "correct" else "0"
                if score.group(1) != expected_score:
                    errors.append(f"{qlabel}: ScoreとResultが一致しません")
                if not selected and result_value != "unknown":
                    errors.append(f"{qlabel}: 未選択のResultはunknownでなければなりません")
                if selected == ["E"] and result_value != "unknown":
                    errors.append(f"{qlabel}: E選択時のResultはunknownでなければなりません")
                if selected and selected[0] in "ABCD" and result_value == "unknown":
                    errors.append(f"{qlabel}: A〜D選択時のResultをunknownにはできません")
                if re.search(r"^#### 解説$", grade, re.MULTILINE):
                    errors.append(f"{qlabel}: 通常Sessionの採点欄に解説があります")

                reviews.append(
                    {
                        "source": path.name,
                        "session": number,
                        "qno": qno,
                        "card_id": card_id,
                        "graded_on": graded_on or "",
                        "stage": int(stage_text.rstrip("+")) if stage_text else None,
                        "result": result_value,
                        "next_review": next_review.group(1),
                        "block": block,
                        "label": qlabel,
                    }
                )

            audit = re.search(
                r"^- Grading Audit: questions=(\d+), graded=(\d+), correct=(\d+), "
                r"incorrect=(\d+), unknown=(\d+)$",
                body,
                re.MULTILINE,
            )
            expected_audit = (
                len(questions),
                len(questions),
                session_results["correct"],
                session_results["incorrect"],
                session_results["unknown"],
            )
            if not audit or tuple(map(int, audit.groups())) != expected_audit:
                errors.append(
                    f"{label}: Grading Auditが実際の結果内訳 {expected_audit} と一致しません"
                )

    latest_by_card: dict[str, dict[str, object]] = {}
    for review in reviews:
        card_id = str(review["card_id"])
        key = (
            str(review["graded_on"]),
            str(review["source"]),
            int(review["session"]),
            int(review["qno"]),
        )
        previous = latest_by_card.get(card_id)
        if previous is None:
            latest_by_card[card_id] = review
            continue
        previous_key = (
            str(previous["graded_on"]),
            str(previous["source"]),
            int(previous["session"]),
            int(previous["qno"]),
        )
        if key > previous_key:
            latest_by_card[card_id] = review

    for card_id, review in latest_by_card.items():
        label = str(review["label"])
        card = cards.get(card_id)
        if card is None:
            errors.append(f"{label}: カード一覧にCard IDがありません")
            continue
        if card["last_result"] != review["result"]:
            errors.append(f"{label}: 最新採点とカード一覧のLast Resultが一致しません")
        if card["next_review"] != review["next_review"]:
            errors.append(f"{label}: 最新採点とカード一覧のNext Reviewが一致しません")
        if card["last_reviewed"] != review["graded_on"]:
            errors.append(f"{label}: 最新採点日とカード一覧のLast Reviewedが一致しません")
        source_stage = review["stage"]
        if source_stage is not None:
            expected_stage = 0 if review["result"] != "correct" else int(source_stage) + 1
            if card["stage"] != str(expected_stage):
                errors.append(
                    f"{label}: 最新採点から期待されるStage {expected_stage} とカード一覧が一致しません"
                )

    expected_wrong: set[tuple[str, int, int, str]] = set()
    for review in reviews:
        if review["result"] == "correct":
            continue
        key = (
            str(review["source"]),
            int(review["session"]),
            int(review["qno"]),
            str(review["card_id"]),
        )
        expected_wrong.add(key)
        label = str(review["label"])
        records = wrong_records.get(key, [])
        if len(records) != 1:
            errors.append(
                f"{label}: 元Sessionを含む照合キーに一致する誤答記録がちょうど一つではありません"
            )
            continue
        record = records[0]
        if not all(section in record for section in ("### 問題", "### 模範解答", "### 解説")):
            errors.append(f"{label}: 誤答記録に問題・模範解答・解説がそろっていません")
            continue
        options = dict(
            re.findall(r"^- \[[ x]\] ([A-D])\. (.+)$", str(review["block"]), re.MULTILINE)
        )
        if set(options) != set("ABCD"):
            errors.append(f"{label}: 元SessionにA〜Dの選択肢がそろっていません")
            continue
        for choice, option in options.items():
            formatted = re.search(
                rf"^- {choice}: (.+)<br>\n  → (.+)$", record, re.MULTILINE
            )
            if not formatted:
                errors.append(
                    f"{label}: {choice}が『元の選択肢<br> 改行 → 解説』形式ではありません"
                )
                continue
            recorded_option, explanation = formatted.groups()
            if recorded_option.strip() != option.strip():
                errors.append(f"{label}: {choice}の元の選択肢本文がSessionと一致しません")
            if explanation.strip() == option.strip():
                errors.append(f"{label}: {choice}の解説が選択肢本文の再掲だけです")
            if any(
                phrase in explanation
                for phrase in (
                    "この用語が表す役割・概念です",
                    "設問で示された条件・役割を表す説明です",
                    "設問の条件には合わない",
                    "正解とは区別します",
                    "正解の選択肢です",
                )
            ):
                errors.append(f"{label}: {choice}の説明が禁止された定型文です")

    for key, records in wrong_records.items():
        if key not in expected_wrong:
            errors.append(
                f"誤答記録 {key[0]} Session {key[1]} Q{key[2]} {key[3]}: "
                "対応する誤答・不明の採点がありません"
            )
        if len(records) > 1:
            errors.append(
                f"誤答記録 {key[0]} Session {key[1]} Q{key[2]} {key[3]}: 重複しています"
            )

    if errors:
        print("検査失敗:")
        print("\n".join(f"- {error}" for error in errors))
        return 1
    print(f"検査成功: graded session {checked}件")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
