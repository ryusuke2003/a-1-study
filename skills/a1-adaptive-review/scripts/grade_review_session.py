#!/usr/bin/env python3
"""Grade one A-1 Session with validation, cooperative locking and rollback."""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import contextmanager, nullcontext
from datetime import date, timedelta
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from typing import Any
from urllib.parse import unquote, urlsplit


DEFAULT_ROOT = Path(__file__).resolve().parents[3]
FORBIDDEN_EXPLANATIONS = (
    "この用語が表す役割・概念です",
    "設問で示された条件・役割を表す説明です",
    "設問の条件には合わない",
    "正解とは区別します",
    "正解の選択肢です",
)
FINAL_RESULTS = {"correct", "incorrect", "unknown"}
CARDS_PATH = Path("\u5fa9\u7fd2\u30ab\u30fc\u30c9/\u30ab\u30fc\u30c9\u4e00\u89a7.md")
SESSIONS_PATH = Path("\u5b66\u7fd2\u8a18\u9332/\u5fa9\u7fd2\u554f\u984c")
NOTES_PATH = Path("\u5b66\u3093\u3060\u3053\u3068")
LOCK_NAME = ".a1-grading.lock"


class GradingError(ValueError):
    """Raised when grading input or repository state is invalid."""



def positive_integer(value: Any, label: str) -> int:
    # Legacy digit strings remain valid; bool and fractional numbers never are.
    if type(value) is int and value > 0:
        return value
    if isinstance(value, str) and re.fullmatch(r"[1-9]\d*", value):
        return int(value)
    raise GradingError(f"{label} must be a positive integer")


def strict_date(value: Any, label: str) -> date:
    try:
        if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            raise ValueError(value)
        return date.fromisoformat(value)
    except ValueError as error:
        raise GradingError(f"{label} must be a valid YYYY-MM-DD date") from error


def inside(root: Path, path: Path, label: str) -> Path:
    resolved = path.resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise GradingError(f"{label} must stay inside {root}")
    return resolved


def session_path_for(root: Path, filename: str) -> Path:
    if not isinstance(filename, str) or not filename:
        raise GradingError("session_file must be a non-empty string")
    path = inside(root, root / filename, "session_file")
    directory = inside(root, root / SESSIONS_PATH, "Session directory")
    if path.parent != directory or path.suffix != ".md" or path.name == "README.md":
        raise GradingError("session_file must name a Markdown file in the review Session directory")
    return path


def card_rows(text: str) -> dict[str, list[str]]:
    rows: dict[str, list[str]] = {}
    for line in text.splitlines():
        if not re.match(r"^\s*\|\s*A1-", line):
            continue
        values = [v.strip() for v in line.strip().strip("|").split("|")]
        if len(values) != 8:
            raise GradingError("Card table must have eight columns")
        cid = values[0]
        if not re.fullmatch(r"A1-\d+", cid) or cid in rows:
            raise GradingError(f"Invalid or duplicate Card ID: {cid}")
        if not re.fullmatch(r"\d+", values[6]):
            raise GradingError(f"{cid}: Stage must be a non-negative integer")
        if values[7] not in FINAL_RESULTS | {"unreviewed", "new"}:
            raise GradingError(f"{cid}: unsupported Last Result")
        strict_date(values[5], f"{cid} Next Review")
        if values[4] != "-":
            strict_date(values[4], f"{cid} Last Reviewed")
        rows[cid] = values
    return rows


def validate_card_state(rows: dict[str, list[str]], questions: list[dict[str, Any]],
                        graded_on: str, session_text: str) -> None:
    day = strict_date(graded_on, "graded_on")
    created = field(session_text, "Created")
    if created and day < strict_date(created, "Created"):
        raise GradingError("graded_on must not precede Session creation")
    ids = [q["card_id"] for q in questions]
    if len(ids) != len(set(ids)):
        raise GradingError("A Session must not grade the same Card ID twice")
    for question in questions:
        cid = question["card_id"]
        if cid not in rows:
            raise GradingError(f"Unknown Card ID: {cid}")
        row = rows[cid]
        if int(row[6]) != question["stage"]:
            raise GradingError(f"{cid}: stale Stage; reconcile the Session with the current card before grading")
        if row[4] != "-" and day < strict_date(row[4], "Last Reviewed"):
            raise GradingError(f"{cid}: grading must not move Last Reviewed backwards")


def card_digests(rows: dict[str, list[str]], questions: list[dict[str, Any]]) -> dict[str, str]:
    return {q["card_id"]: hashlib.sha256(json.dumps(rows[q["card_id"]], ensure_ascii=False).encode()).hexdigest()
            for q in questions}


@contextmanager
def grading_lock(root: Path):
    """Serialize cooperating writers. A killed process leaves a visible recovery lock."""
    path = root / LOCK_NAME
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as error:
        raise GradingError(f"Grading is locked: {path}; check the other process before removing a stale lock") from error
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(f"pid={os.getpid()}\n")
        yield
    finally:
        path.unlink(missing_ok=True)


def unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise GradingError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def field(text: str, name: str, pattern: str = r"[^\n]+") -> str | None:
    match = re.search(rf"^- {re.escape(name)}: ({pattern})$", text, re.MULTILINE)
    return match.group(1).strip() if match else None


def find_session(text: str, number: int) -> tuple[int, int, str]:
    matches = list(re.finditer(r"^## Session (\d+)\n", text, re.MULTILINE))
    numbers = [int(match.group(1)) for match in matches]
    if len(numbers) != len(set(numbers)):
        raise GradingError("Session numbers must be unique within the file")
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
        raw_options = re.findall(
            r"^- \[([^\]]*)\] ([A-Za-z])\. (.*)$", block, re.MULTILINE
        )
        if [choice for _, choice, _ in raw_options] != list("ABCDE"):
            raise GradingError(f"Q{match.group(1)}: require exactly A, B, C, D, E in order")
        if any(mark not in (" ", "x", "X") or not text.strip()
               for mark, _, text in raw_options):
            raise GradingError(f"Q{match.group(1)}: invalid checkbox or empty option")
        if raw_options[-1][2].strip() != "わかりません":
            raise GradingError(f"Q{match.group(1)}: E must be the unknown option")
        options = {choice: text for _, choice, text in raw_options if choice in "ABCD"}
        selected = [(choice, text) for mark, choice, text in raw_options if mark in ("x", "X")]
        problem = re.search(
            r"^### 問題\n\n(.+?)\n\n- \[[ xX]\] A\.", block, re.MULTILINE | re.DOTALL
        )
        source = re.search(r"^- Source: \[[^]]+\]\(([^)]+)\)$", block, re.MULTILINE)
        card_id = field(block, "Card ID", r"A1-\d+")
        stage_text = field(block, "Stage", r"\d+\+?")
        for name, value in (("Card ID", card_id), ("Stage", stage_text)):
            if value is None or len(re.findall(rf"^- {re.escape(name)}:", block, re.MULTILINE)) != 1:
                raise GradingError(f"Q{match.group(1)}: require one valid {name}")
        if not problem or not problem.group(1).strip():
            raise GradingError(f"Q{match.group(1)}: problem text is missing")
        if len(selected) > 1:
            raise GradingError(f"Q{match.group(1)}: multiple options are selected")
        questions.append(
            {
                "q": int(match.group(1)),
                "start": match.start(),
                "end": end,
                "block": block,
                "card_id": card_id,
                "stage": int(stage_text.rstrip("+")),
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


def validate_session(session_text: str) -> list[dict[str, Any]]:
    """Validate the same Session contract in prepare, preflight and apply."""
    header = re.split(r"^### Q\d+\n", session_text, maxsplit=1, flags=re.MULTILINE)[0]
    for name in ("Status", "Question Count"):
        if len(re.findall(rf"^- {re.escape(name)}:", header, re.MULTILINE)) != 1:
            raise GradingError(f"Session must contain exactly one {name}")
    declared = field(header, "Question Count", r"\d+")
    questions = parse_questions(session_text)
    if declared is None or int(declared) < 1 or int(declared) != len(questions):
        raise GradingError("Question Count must be positive and match every question")
    return questions


def session_digest(session_text: str) -> str:
    """Detect a stale draft; this is not a security signature or answer key."""
    return hashlib.sha256(session_text.encode("utf-8")).hexdigest()


def load_card_points(cards_path: Path) -> dict[str, str]:
    return {cid: row[2] for cid, row in card_rows(cards_path.read_text(encoding="utf-8")).items()}


def source_excerpt(session_path: Path, source: str | None, terms: list[str], *,
                   root: Path | None = None,
                   cache: dict[Path, list[str]] | None = None) -> str:
    if not source:
        return ""
    root = root or session_path.parents[2]
    url = urlsplit(source)
    if url.scheme or url.netloc or url.query:
        raise GradingError("Source must be a local Markdown note, not an external URL")
    notes = inside(root, root / NOTES_PATH, "Note directory")
    path = inside(notes, session_path.parent / unquote(url.path), "Source")
    if path.suffix != ".md" or not path.is_file():
        raise GradingError(f"Source note is missing or not Markdown: {path}")
    # Cache lasts for one prepare only: no stale content across separate invocations.
    if cache is None:
        cache = {}
    if path not in cache:
        cache[path] = [part.strip() for part in re.split(r"\n\s*\n", path.read_text(encoding="utf-8")) if part.strip()]
    paragraphs = cache[path]
    tokens: list[str] = []
    for term in terms:
        tokens.extend(re.findall(r"[A-Za-z][A-Za-z0-9+./-]*|[\u4e00-\u9fa5\u30a1-\u30f6\u30fc]{2,}", term))
    scored = sorted(
        ((sum(token.lower() in paragraph.lower() for token in tokens), paragraph) for paragraph in paragraphs),
        reverse=True,
    )
    excerpt = "\n\n".join(paragraph for score, paragraph in scored[:3] if score > 0)
    return (excerpt or "\n\n".join(paragraphs[:2]))[:2400]


def make_draft(root: Path, session_file: str, session_number: int, graded_on: str) -> dict[str, Any]:
    root = root.resolve()
    session_number = positive_integer(session_number, "session")
    session_path = session_path_for(root, session_file)
    text = session_path.read_text(encoding="utf-8")
    _, _, session_text = find_session(text, session_number)
    status = field(session_text, "Status")
    if status != "awaiting_answers":
        raise GradingError(f"Status が awaiting_answers ではありません: {status}")
    questions = validate_session(session_text)
    rows = card_rows(inside(root, root / CARDS_PATH, "Card table").read_text(encoding="utf-8"))
    validate_card_state(rows, questions, graded_on, session_text)
    points = {cid: row[2] for cid, row in rows.items()}
    cache: dict[Path, list[str]] = {}
    entries: list[dict[str, Any]] = []
    for question in questions:
        card_point = points.get(question["card_id"] or "", "")
        excerpt = source_excerpt(
            session_path,
            question["source"],
            [card_point, question["problem"], *question["options"].values()],
            root=root, cache=cache,
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
        "session_file": session_path.relative_to(root).as_posix(),
        "session": session_number,
        "session_sha256": session_digest(session_text),
        "card_sha256": card_digests(rows, questions),
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
        raw = explanations[choice]
        if not isinstance(raw, str) or "\n" in raw or "\r" in raw:
            raise GradingError(f"Q{question['q']}: {choice} explanation must be a single-line string")
        explanation = raw.strip()
        if not explanation or explanation.startswith("DRAFT:"):
            raise GradingError(f"Q{question['q']}: {choice}の下書きを完成させてください")
        if explanation == question["options"][choice] or len(explanation) < 12:
            raise GradingError(f"Q{question['q']}: {choice}の解説が具体的ではありません")
        if any(phrase in explanation for phrase in FORBIDDEN_EXPLANATIONS):
            raise GradingError(f"Q{question['q']}: {choice}の解説に禁止定型文があります")


def next_review(graded_on: str, source_stage: int, result: str) -> tuple[int, str]:
    day = strict_date(graded_on, "graded_on")
    if result != "correct":
        return 0, str(day + timedelta(days=1))
    stage = source_stage + 1
    intervals = {1: 3, 2: 7, 3: 14, 4: 30, 5: 60}
    if stage > 28:
        raise GradingError("Review interval exceeds the supported calendar range")
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
    return clean[:position] + "\n\n" + grade.rstrip() + "\n\n" + clean[position:].lstrip("\n")


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


def mistake_record(item: dict[str, Any], session_file: str, session_number: int) -> str:
    question = item
    entry = item["entry"]
    selected = question["selected"]
    your_answer = "未選択" if selected is None else f"{selected}. {question['selected_text']}"
    correct = entry["correct_answer"]
    options = question["options"]
    lines = [
        f"## Session {session_number} / Q{question['q']}: {question['card_id']}",
        "",
        f"- Source Session: [{Path(session_file).stem} 復習問題](../復習問題/{Path(session_file).name}#session-{session_number})",
        f"- Your Answer: {your_answer}",
        f"- Correct Answer: {correct}. {options[correct]}",
        "", "### 問題", "", question["problem"], "", "### 模範解答", "",
        f"{correct}. {options[correct]}", "", "### 解説", "",
    ]
    for choice in "ABCD":
        lines.extend([f"- {choice}: {options[choice]}<br>", f"  → {entry['explanations'][choice]}"])
    return "\n".join(lines) + "\n"


def update_mistakes(current: str, applied: list[dict[str, Any]], session_file: str, session_number: int, graded_on: str) -> str:
    records = [mistake_record(item, session_file, session_number) for item in applied if item["result"] != "correct"]
    if not records:
        return current
    base = current or f"# {graded_on} 間違えた問題\n"
    for item in applied:
        if item["result"] == "correct":
            continue
        heading = f"## Session {session_number} / Q{item['q']}: {item['card_id']}"
        matches = re.finditer(rf"^{re.escape(heading)}\n(.*?)(?=^## |\Z)", base, re.MULTILINE | re.DOTALL)
        for match in matches:
            source = re.search(r"^- Source Session: \[[^]]*\]\(([^)]+)\)$", match[1], re.MULTILINE)
            if source and Path(unquote(urlsplit(source[1]).path)).name != Path(session_file).name:
                continue
            raise GradingError(f"誤答記録が既に存在します: {heading}")
    return base.rstrip() + "\n\n" + "\n\n".join(record.rstrip() for record in records) + "\n"


def atomic_write(path: Path, text: str | bytes, *, mode: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = mode if mode is not None else (path.stat().st_mode & 0o777 if path.exists() else 0o644)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile("wb", dir=path.parent, delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(text.encode("utf-8") if isinstance(text, str) else text)
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def apply_manifest(root: Path, manifest_path: Path, dry_run: bool = False) -> Counter[str]:
    root = root.resolve()
    with nullcontext() if dry_run else grading_lock(root):
        return _apply_manifest(root, manifest_path, dry_run)


def _apply_manifest(root: Path, manifest_path: Path, dry_run: bool = False) -> Counter[str]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"), object_pairs_hook=unique_json_object)
    if not isinstance(manifest, dict):
        raise GradingError("Manifest must be a JSON object")
    session_number = positive_integer(manifest.get("session"), "session")
    graded_on = manifest.get("graded_on")
    strict_date(graded_on, "graded_on")
    session_path = session_path_for(root, manifest.get("session_file"))
    session_file = session_path.relative_to(root).as_posix()
    session_bytes = session_path.read_bytes()
    session_text = session_bytes.decode("utf-8").replace("\r\n", "\n")
    start, end, session_block = find_session(session_text, session_number)
    if field(session_block, "Status") != "awaiting_answers":
        raise GradingError("対象Sessionは awaiting_answers ではありません")
    questions = validate_session(session_block)
    if "session_sha256" in manifest and manifest["session_sha256"] != session_digest(session_block):
        raise GradingError("Session changed since prepare; regenerate and review the full manifest")
    raw_entries = manifest.get("questions")
    if not isinstance(raw_entries, list):
        raise GradingError("questions は配列で指定してください")
    if any(not isinstance(entry, dict) for entry in raw_entries):
        raise GradingError("Each question entry must be a JSON object")
    entries = {positive_integer(entry.get("q"), "q"): entry for entry in raw_entries}
    if set(entries) != {question["q"] for question in questions} or len(entries) != len(raw_entries):
        raise GradingError("マニフェストはSessionの全Q番号を重複なく含めてください")
    cards_path = inside(root, root / CARDS_PATH, "Card table")
    cards_bytes = cards_path.read_bytes()
    cards_text = cards_bytes.decode("utf-8").replace("\r\n", "\n")
    rows = card_rows(cards_text)
    validate_card_state(rows, questions, graded_on, session_block)
    if "card_sha256" in manifest and manifest["card_sha256"] != card_digests(rows, questions):
        raise GradingError("Card changed since prepare; regenerate and review the manifest")
    for question in questions:
        entry = entries[question["q"]]
        for key, value in (("card_id", question["card_id"]), ("selected", question["selected"] or "unselected")):
            if key in entry and entry[key] != value:
                raise GradingError(f"Q{question['q']}: manifest {key} does not match the Session")
    new_session_block, applied, results = update_session(session_block, questions, entries, graded_on)
    new_session_text = session_text[:start] + new_session_block + session_text[end:]
    new_cards_text = update_cards(cards_text, applied, graded_on)
    mistakes_path = root / "学習記録" / "間違えた問題" / f"{graded_on}.md"
    mistakes_path = inside(root, mistakes_path, "Mistake record")
    mistakes_existed = mistakes_path.exists()
    mistakes_bytes = mistakes_path.read_bytes() if mistakes_existed else None
    mistakes_text = mistakes_bytes.decode("utf-8").replace("\r\n", "\n") if mistakes_bytes is not None else ""
    new_mistakes_text = update_mistakes(
        mistakes_text, applied, session_file, session_number, graded_on
    )
    if dry_run:
        return results
    originals = {
        session_path: session_bytes,
        cards_path: cards_bytes,
        mistakes_path: mistakes_bytes,
    }
    try:
        atomic_write(session_path, new_session_text)
        atomic_write(cards_path, new_cards_text)
        if new_mistakes_text != mistakes_text:
            atomic_write(mistakes_path, new_mistakes_text)
        verifier = root / "skills" / "a1-adaptive-review" / "scripts" / "verify_review_sessions.py"
        completed = subprocess.run([sys.executable, str(verifier)], cwd=root, text=True, capture_output=True, timeout=30)
        if completed.returncode:
            raise GradingError("自動検査に失敗しました:\n" + completed.stdout + completed.stderr)
    except BaseException:
        for path, content in originals.items():
            if content is None:
                if path.exists():
                    path.unlink()
            else:
                atomic_write(path, content)
        raise
    return results


def cleanup_moved_comments(root: Path, dry_run: bool = False) -> int:
    with nullcontext() if dry_run else grading_lock(root):
        return _cleanup_moved_comments(root, dry_run)


def _cleanup_moved_comments(root: Path, dry_run: bool = False) -> int:
    pattern = re.compile(r"\n?<!-- 採点日\d{4}-\d{2}-\d{2}へ移動済み\n.*?-->\n?", re.DOTALL)
    removed = 0
    for path in sorted((root / "学習記録" / "間違えた問題").glob("*.md")):
        path = inside(root, path, "Mistake record")
        text = path.read_text(encoding="utf-8")
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
                if args.output.resolve().is_relative_to(root):
                    raise GradingError("Draft output must be outside the repository (for example /tmp)")
                atomic_write(args.output, output, mode=0o600)
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
    except (GradingError, KeyError, ValueError, OSError, subprocess.TimeoutExpired, OverflowError) as error:
        print(f"エラー: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
