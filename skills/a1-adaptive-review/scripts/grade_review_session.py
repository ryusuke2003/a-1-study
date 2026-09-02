#!/usr/bin/env python3
"""Prepare and atomically apply grading for one A-1 review session."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date, timedelta
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from typing import Any


DEFAULT_ROOT = Path(__file__).resolve().parents[3]
FORBIDDEN_EXPLANATIONS = (
    "この用語が表す役割・概念です",
    "設問で示された条件・役割を表す説明です",
    "設問の条件には合わない",
    "正解とは区別します",
    "正解の選択肢です",
)
FINAL_RESULTS = {"correct", "incorrect", "unknown"}


class GradingError(ValueError):
    """Raised when grading input or repository state is invalid."""


def field(text: str, name: str, pattern: str = r"[^\n]+") -> str | None:
    match = re.search(rf"^- {re.escape(name)}: ({pattern})$", text, re.MULTILINE)
    return match.group(1).strip() if match else None


def find_session(text: str, number: int) -> tuple[int, int, str]:
    matches = list(re.finditer(r"^## Session (\d+)\n", text, re.MULTILINE))
    for index, match in enumerate(matches):
        if int(match.group(1)) != number:
            continue
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        return match.start(), end, text[match.start():end]
    raise GradingError(f"Session {number} が見つかりません")


def parse_questions(session_text: str) -> list[dict[str, Any]]:
    matches = list(re.finditer(r"^### Q(\d+)\n", session_text, re.MULTILINE))
    questions: list[dict[str, Any]] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(session_text)
        block = session_text[match.start():end].rstrip() + "\n"
        options = dict(re.findall(r"^- \[[ xX]\] ([A-D])\. (.+)$", block, re.MULTILINE))
        selected = re.findall(r"^- \[[xX]\] ([A-E])\. (.+)$", block, re.MULTILINE)
        problem = re.search(
            r"^### 問題\n\n(.+?)\n\n- \[[ xX]\] A\.", block, re.MULTILINE | re.DOTALL
        )
        source = re.search(r"^- Source: \[[^]]+\]\(([^)]+)\)$", block, re.MULTILINE)
        if set(options) != set("ABCD"):
            raise GradingError(f"Q{match.group(1)}: A〜Dの選択肢がそろっていません")
        if len(selected) > 1:
            raise GradingError(f"Q{match.group(1)}: 選択済みの選択肢が複数あります")
        questions.append(
            {
                "q": int(match.group(1)),
                "start": match.start(),
                "end": end,
                "block": block,
                "card_id": field(block, "Card ID", r"A1-\d+"),
                "stage": int((field(block, "Stage", r"\d+\+?") or "0").rstrip("+")),
                "problem": problem.group(1).strip() if problem else "",
                "options": options,
                "selected": selected[0][0] if selected else None,
                "selected_text": selected[0][1] if selected else None,
                "source": source.group(1) if source else None,
            }
        )
    numbers = [question["q"] for question in questions]
    if numbers != list(range(1, len(questions) + 1)):
        raise GradingError("Q番号がQ1から連番ではありません")
    return questions


def load_card_points(cards_path: Path) -> dict[str, str]:
    points: dict[str, str] = {}
    for line in cards_path.read_text().splitlines():
        if not re.match(r"^\| A1-\d+ \|", line):
            continue
        values = [value.strip() for value in line.strip().strip("|").split("|")]
        if len(values) == 8:
            points[values[0]] = values[2]
    return points


def source_excerpt(session_path: Path, source: str | None, terms: list[str]) -> str:
    if not source:
        return ""
    path = (session_path.parent / source.split("#", 1)[0]).resolve()
    if not path.exists():
        return ""
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", path.read_text()) if part.strip()]
    tokens: list[str] = []
    for term in terms:
        tokens.extend(re.findall(r"[A-Za-z][A-Za-z0-9+./-]*|[一-龥ァ-ヶー]{2,}", term))
    scored = sorted(
        ((sum(token.lower() in paragraph.lower() for token in tokens), paragraph) for paragraph in paragraphs),
        reverse=True,
    )
    excerpt = "\n\n".join(paragraph for score, paragraph in scored[:3] if score > 0)
    return (excerpt or "\n\n".join(paragraphs[:2]))[:2400]


def make_draft(root: Path, session_file: str, session_number: int, graded_on: str) -> dict[str, Any]:
    session_path = (root / session_file).resolve()
    text = session_path.read_text()
    _, _, session_text = find_session(text, session_number)
    status = field(session_text, "Status")
    if status != "awaiting_answers":
        raise GradingError(f"Status が awaiting_answers ではありません: {status}")
    questions = parse_questions(session_text)
    declared = int(field(session_text, "Question Count", r"\d+") or "-1")
    if declared != len(questions):
        raise GradingError("Question Count が実際の設問数と一致しません")
    points = load_card_points(root / "復習カード" / "カード一覧.md")
    entries: list[dict[str, Any]] = []
    for question in questions:
        card_point = points.get(question["card_id"] or "", "")
        excerpt = source_excerpt(
            session_path,
            question["source"],
            [card_point, question["problem"], *question["options"].values()],
        )
        forced_unknown = question["selected"] in (None, "E")
        entries.append(
            {
                "q": question["q"],
                "selected": question["selected"] or "unselected",
                "result": "unknown" if forced_unknown else "REVIEW",
                "correct_answer": "REVIEW",
                "card_id": question["card_id"],
                "card_point": card_point,
                "problem": question["problem"],
                "options": question["options"],
                "source_excerpt": excerpt,
                "explanations": {
                    choice: f"DRAFT: {option}について、カード要点とノートを基に定義・役割・正誤理由を具体化する"
                    for choice, option in question["options"].items()
                },
            }
        )
    return {
        "session_file": str(Path(session_file)),
        "session": session_number,
        "graded_on": graded_on,
        "questions": entries,
    }


def expected_result(selected: str | None, correct_answer: str) -> str:
    if selected in (None, "E"):
        return "unknown"
    return "correct" if selected == correct_answer else "incorrect"


def validate_explanations(entry: dict[str, Any], question: dict[str, Any]) -> None:
    explanations = entry.get("explanations")
    if not isinstance(explanations, dict) or set(explanations) != set("ABCD"):
        raise GradingError(f"Q{question['q']}: A〜Dの解説がそろっていません")
    for choice in "ABCD":
        explanation = str(explanations[choice]).strip()
        if not explanation or explanation.startswith("DRAFT:"):
            raise GradingError(f"Q{question['q']}: {choice}の下書きを完成させてください")
        if explanation == question["options"][choice] or len(explanation) < 12:
            raise GradingError(f"Q{question['q']}: {choice}の解説が具体的ではありません")
        if any(phrase in explanation for phrase in FORBIDDEN_EXPLANATIONS):
            raise GradingError(f"Q{question['q']}: {choice}の解説に禁止定型文があります")


def next_review(graded_on: str, source_stage: int, result: str) -> tuple[int, str]:
    day = date.fromisoformat(graded_on)
    if result != "correct":
        return 0, str(day + timedelta(days=1))
    stage = source_stage + 1
    intervals = {1: 3, 2: 7, 3: 14, 4: 30, 5: 60}
    days = intervals.get(stage, 120 * (2 ** max(stage - 6, 0)))
    return stage, str(day + timedelta(days=days))


def grade_block(result: str, review_date: str) -> str:
    score = "100" if result == "correct" else "0"
    return f"### 採点\n\nScore: {score} / 100\nResult: {result}\nNext Review: {review_date}\n"


def replace_grade(question_block: str, grade: str) -> str:
    clean = re.sub(r"\n### 採点\n.*?\Z", "\n", question_block, flags=re.DOTALL).rstrip() + "\n"
    option_e = list(re.finditer(r"^- \[[ xX]\] E\. .+$", clean, re.MULTILINE))
    if len(option_e) != 1:
        raise GradingError("Eの選択肢がちょうど一つではありません")
    position = option_e[0].end()
    return clean[:position] + "\n\n" + grade.rstrip() + "\n" + clean[position:].lstrip("\n")


def update_session(
    session_text: str,
    questions: list[dict[str, Any]],
    entries: dict[int, dict[str, Any]],
    graded_on: str,
) -> tuple[str, list[dict[str, Any]], Counter[str]]:
    results: Counter[str] = Counter()
    applied: list[dict[str, Any]] = []
    updated = session_text
    for question in reversed(questions):
        entry = entries[question["q"]]
        correct = str(entry.get("correct_answer", ""))
        result = str(entry.get("result", ""))
        if correct not in "ABCD" or len(correct) != 1:
            raise GradingError(f"Q{question['q']}: correct_answer はA〜Dで指定してください")
        derived = expected_result(question["selected"], correct)
        if result != derived:
            raise GradingError(f"Q{question['q']}: result={result} は選択状態と正解から求めた {derived} と一致しません")
        if result != "correct":
            validate_explanations(entry, question)
        stage, review_date = next_review(graded_on, question["stage"], result)
        new_block = replace_grade(question["block"], grade_block(result, review_date))
        updated = updated[:question["start"]] + new_block + updated[question["end"]:]
        results[result] += 1
        applied.append({**question, "entry": entry, "result": result, "new_stage": stage, "review_date": review_date})
    applied.reverse()
    updated = re.sub(r"^- Status: .+$", "- Status: graded", updated, count=1, flags=re.MULTILINE)
    updated = re.sub(r"^- Graded: .+\n", "", updated, flags=re.MULTILINE)
    updated = re.sub(r"^- Grading Audit: .+\n", "", updated, flags=re.MULTILINE)
    audit = (
        f"- Graded: {graded_on}\n"
        f"- Grading Audit: questions={len(questions)}, graded={len(questions)}, "
        f"correct={results['correct']}, incorrect={results['incorrect']}, unknown={results['unknown']}\n"
    )
    status = re.search(r"^- Status: graded$", updated, re.MULTILINE)
    if not status:
        raise GradingError("Statusを更新できませんでした")
    updated = updated[:status.end()] + "\n" + audit.rstrip() + updated[status.end():]
    return updated, applied, results


def update_cards(cards_text: str, applied: list[dict[str, Any]], graded_on: str) -> str:
    latest = {item["card_id"]: item for item in applied}
    found: set[str] = set()
    lines: list[str] = []
    for line in cards_text.splitlines():
        match = re.match(r"^\| (A1-\d+) \|", line)
        if not match or match.group(1) not in latest:
            lines.append(line)
            continue
        card_id = match.group(1)
        values = [value.strip() for value in line.strip().strip("|").split("|")]
        if len(values) != 8:
            raise GradingError(f"{card_id}: カード一覧の列数が8ではありません")
        item = latest[card_id]
        values[4] = graded_on
        values[5] = item["review_date"]
        values[6] = str(item["new_stage"])
        values[7] = item["result"]
        lines.append("| " + " | ".join(values) + " |")
        found.add(card_id)
    missing = set(latest) - found
    if missing:
        raise GradingError(f"カード一覧に存在しないID: {', '.join(sorted(missing))}")
    return "\n".join(lines) + "\n"


def wrong_record(item: dict[str, Any], session_file: str, session_number: int) -> str:
    question = item
    entry = item["entry"]
    selected = question["selected"]
    your_answer = "未選択" if selected is None else f"{selected}. {question['selected_text']}"
    correct = entry["correct_answer"]
    options = question["options"]
    lines = [
        f"## Session {session_number} / Q{question['q']}: {question['card_id']}",
        "",
        f"- Source Session: [{Path(session_file).stem} 復習問題](../復習問題/{Path(session_file).name})",
        f"- Your Answer: {your_answer}",
        f"- Correct Answer: {correct}. {options[correct]}",
        "", "### 問題", "", question["problem"], "", "### 模範解答", "",
        f"{correct}. {options[correct]}", "", "### 解説", "",
    ]
    for choice in "ABCD":
        lines.extend([f"- {choice}: {options[choice]}<br>", f"  → {entry['explanations'][choice]}"])
    return "\n".join(lines) + "\n"


def update_wrong(current: str, applied: list[dict[str, Any]], session_file: str, session_number: int, graded_on: str) -> str:
    records = [wrong_record(item, session_file, session_number) for item in applied if item["result"] != "correct"]
    if not records:
        return current
    base = current or f"# {graded_on} 間違った問題\n"
    for item in applied:
        if item["result"] == "correct":
            continue
        heading = f"## Session {session_number} / Q{item['q']}: {item['card_id']}"
        if re.search(rf"^{re.escape(heading)}$", base, re.MULTILINE):
            raise GradingError(f"誤答記録が既に存在します: {heading}")
    return base.rstrip() + "\n\n" + "\n\n".join(record.rstrip() for record in records) + "\n"


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = path.stat().st_mode & 0o777 if path.exists() else 0o644
    with tempfile.NamedTemporaryFile("w", dir=path.parent, delete=False, encoding="utf-8") as handle:
        handle.write(text)
        temporary = Path(handle.name)
    os.chmod(temporary, mode)
    os.replace(temporary, path)


def apply_manifest(root: Path, manifest_path: Path, dry_run: bool = False) -> Counter[str]:
    manifest = json.loads(manifest_path.read_text())
    session_file = str(manifest["session_file"])
    session_number = int(manifest["session"])
    graded_on = str(manifest["graded_on"])
    date.fromisoformat(graded_on)
    session_path = (root / session_file).resolve()
    try:
        session_path.relative_to(root.resolve())
    except ValueError as error:
        raise GradingError("session_file はリポジトリ内を指定してください") from error
    session_text = session_path.read_text()
    start, end, session_block = find_session(session_text, session_number)
    if field(session_block, "Status") != "awaiting_answers":
        raise GradingError("対象Sessionは awaiting_answers ではありません")
    questions = parse_questions(session_block)
    raw_entries = manifest.get("questions")
    if not isinstance(raw_entries, list):
        raise GradingError("questions は配列で指定してください")
    entries = {int(entry["q"]): entry for entry in raw_entries}
    if set(entries) != {question["q"] for question in questions} or len(entries) != len(raw_entries):
        raise GradingError("マニフェストはSessionの全Q番号を重複なく含めてください")
    new_session_block, applied, results = update_session(session_block, questions, entries, graded_on)
    new_session_text = session_text[:start] + new_session_block + session_text[end:]
    cards_path = root / "復習カード" / "カード一覧.md"
    cards_text = cards_path.read_text()
    new_cards_text = update_cards(cards_text, applied, graded_on)
    wrong_path = root / "学習記録" / "間違った問題" / f"{graded_on}.md"
    wrong_existed = wrong_path.exists()
    wrong_text = wrong_path.read_text() if wrong_existed else ""
    new_wrong_text = update_wrong(wrong_text, applied, session_file, session_number, graded_on)
    if dry_run:
        return results
    originals = {session_path: session_text, cards_path: cards_text, wrong_path: wrong_text if wrong_existed else None}
    try:
        atomic_write(session_path, new_session_text)
        atomic_write(cards_path, new_cards_text)
        if new_wrong_text != wrong_text:
            atomic_write(wrong_path, new_wrong_text)
        verifier = root / "skills" / "a1-adaptive-review" / "scripts" / "verify_review_sessions.py"
        completed = subprocess.run([sys.executable, str(verifier)], cwd=root, text=True, capture_output=True)
        if completed.returncode:
            raise GradingError("自動検査に失敗しました:\n" + completed.stdout + completed.stderr)
    except Exception:
        for path, content in originals.items():
            if content is None:
                if path.exists():
                    path.unlink()
            else:
                atomic_write(path, content)
        raise
    return results


def cleanup_moved_comments(root: Path, dry_run: bool = False) -> int:
    pattern = re.compile(r"\n?<!-- 採点日\d{4}-\d{2}-\d{2}へ移動済み\n.*?-->\n?", re.DOTALL)
    removed = 0
    for path in sorted((root / "学習記録" / "間違った問題").glob("*.md")):
        text = path.read_text()
        cleaned, count = pattern.subn("\n", text)
        if count:
            removed += count
            if not dry_run:
                atomic_write(path, cleaned.rstrip() + "\n")
    return removed


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare = subparsers.add_parser("prepare", help="カード・ノート付き採点マニフェスト下書きを生成")
    prepare.add_argument("session_file")
    prepare.add_argument("--session", type=int, default=1)
    prepare.add_argument("--graded-on", required=True)
    prepare.add_argument("--output", type=Path)
    apply_parser = subparsers.add_parser("apply", help="完成したマニフェストを一括適用")
    apply_parser.add_argument("manifest", type=Path)
    apply_parser.add_argument("--dry-run", action="store_true")
    cleanup = subparsers.add_parser("cleanup-moved-comments", help="日付移動済みHTMLコメント残骸を削除")
    cleanup.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = args.root.resolve()
    try:
        if args.command == "prepare":
            draft = make_draft(root, args.session_file, args.session, args.graded_on)
            output = json.dumps(draft, ensure_ascii=False, indent=2) + "\n"
            if args.output:
                atomic_write(args.output, output)
                print(f"下書きを生成: {args.output} ({len(draft['questions'])}問)")
            else:
                print(output, end="")
        elif args.command == "apply":
            results = apply_manifest(root, args.manifest, args.dry_run)
            mode = "検証成功" if args.dry_run else "採点適用成功"
            print(f"{mode}: correct={results['correct']}, incorrect={results['incorrect']}, unknown={results['unknown']}")
        else:
            removed = cleanup_moved_comments(root, args.dry_run)
            mode = "検出" if args.dry_run else "削除"
            print(f"移動済みコメント残骸: {removed}件{mode}")
    except (GradingError, KeyError, json.JSONDecodeError, OSError) as error:
        print(f"エラー: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
