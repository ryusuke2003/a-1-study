#!/usr/bin/env python3
"""A-1適応復習のMarkdownデータを管理する補助コマンド。"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo


ROOT_FILES = {
    "学んだこと/README.md": """# 学んだこと\n\n学習者が入力した内容を分野別に整理する恒久ノートです。新しい内容は `## YYYY-MM-DD` の下へ追記します。ここで問題は作りません。\n\n- テクノロジ系\n- マネジメント系\n- ストラテジ系\n""",
    "学んだこと/テクノロジ系/README.md": "# テクノロジ系\n\n技術分野の学びを、必要に応じてテーマ別Markdownへ整理します。\n",
    "学んだこと/マネジメント系/README.md": "# マネジメント系\n\n管理分野の学びを、必要に応じてテーマ別Markdownへ整理します。\n",
    "学んだこと/ストラテジ系/README.md": "# ストラテジ系\n\n戦略分野の学びを、必要に応じてテーマ別Markdownへ整理します。\n",
    "復習カード/カード一覧.md": "# 復習カード一覧\n\n`python3 scripts/a1_review.py rebuild` で更新します。\n",
    "学習記録/復習問題/README.md": "# 復習問題\n\n問題Sessionと対応する解答キーを日付単位で保存します。回答前のSession本文には正解を書きません。\n",
    "学習記録/復習問題/解答/README.md": "# 解答キー\n\n対応する問題Sessionの採点用情報です。回答中は参照しません。\n",
    "学習記録/学習履歴.md": "# 学習履歴\n\n採点済みSessionを記録します。\n",
    "進捗/分野別状況.md": "# 分野別状況\n\n`python3 scripts/a1_review.py rebuild` で更新します。\n",
}
INTERVALS = [1, 3, 7, 14, 30, 60]


@dataclass
class Card:
    path: Path
    card_id: str
    title: str
    domain: str
    related_domains: str
    source: str
    created: str
    last_reviewed: str
    next_review: str
    stage: int
    last_result: str
    origin: str


def study_day() -> date:
    now = datetime.now(ZoneInfo("Asia/Tokyo"))
    return (now - timedelta(days=1)).date() if now.hour < 5 else now.date()


def root_path(value: str) -> Path:
    return Path(value).resolve()


def cards_dir(root: Path) -> Path:
    return root / "復習カード" / "カード本文"


def ensure_layout(root: Path) -> None:
    for relative, content in ROOT_FILES.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_text(content, encoding="utf-8")
    cards_dir(root).mkdir(parents=True, exist_ok=True)


def metadata(text: str, key: str) -> str:
    found = re.search(rf"^- {re.escape(key)}: *(.*)$", text, re.MULTILINE)
    if not found:
        raise ValueError(f"必須項目 `{key}` がありません")
    return found.group(1).strip()


def replace_metadata(text: str, key: str, value: str) -> str:
    pattern = rf"^- {re.escape(key)}:.*$"
    if not re.search(pattern, text, re.MULTILINE):
        raise ValueError(f"必須項目 `{key}` がありません")
    return re.sub(pattern, f"- {key}: {value}", text, count=1, flags=re.MULTILINE)


def parse_card(path: Path) -> Card:
    text = path.read_text(encoding="utf-8")
    title = re.search(r"^# (.+)$", text, re.MULTILINE)
    if not title:
        raise ValueError(f"{path}: カードの見出しがありません")
    stage_text = metadata(text, "Stage")
    return Card(
        path=path,
        card_id=metadata(text, "Card ID"),
        title=title.group(1),
        domain=metadata(text, "Domain"),
        related_domains=metadata(text, "Related Domains"),
        source=metadata(text, "Source"),
        created=metadata(text, "Created"),
        last_reviewed=metadata(text, "Last Reviewed"),
        next_review=metadata(text, "Next Review"),
        stage=int(stage_text),
        last_result=metadata(text, "Last Result"),
        origin=metadata(text, "Origin"),
    )


def load_cards(root: Path) -> list[Card]:
    result = []
    for path in sorted(cards_dir(root).glob("*.md")):
        result.append(parse_card(path))
    return result


def next_card_id(cards: list[Card]) -> str:
    numbers = [int(match.group(1)) for card in cards if (match := re.fullmatch(r"A1-(\d{4})", card.card_id))]
    return f"A1-{max(numbers, default=0) + 1:04d}"


def write_card(card: Card, text: str, *, stage: int, result: str, reviewed: date, next_date: date) -> None:
    text = replace_metadata(text, "Stage", str(stage))
    text = replace_metadata(text, "Last Result", result)
    text = replace_metadata(text, "Last Reviewed", reviewed.isoformat())
    text = replace_metadata(text, "Next Review", next_date.isoformat())
    card.path.write_text(text, encoding="utf-8")


def section(text: str, heading: str) -> str:
    matched = re.search(rf"^## {re.escape(heading)}\n(.*?)(?=^## |\Z)", text, re.MULTILINE | re.DOTALL)
    return matched.group(1).strip() if matched else ""


def command_init(args: argparse.Namespace) -> None:
    ensure_layout(root_path(args.root))
    print("A-1復習用のMarkdown構成を初期化しました。")


def command_add_card(args: argparse.Namespace) -> None:
    root = root_path(args.root)
    ensure_layout(root)
    existing = load_cards(root)
    card_id = next_card_id(existing)
    today = study_day().isoformat()
    path = cards_dir(root) / f"{card_id}.md"
    text = f"""# {args.title}

- Card ID: {card_id}
- Domain: {args.domain}
- Related Domains: {args.related_domains or '-'}
- Source: {args.source}
- Origin: registered
- Created: {today}
- Last Reviewed: -
- Next Review: {today}
- Stage: 0
- Last Result: unreviewed

## 確認したい要点

{args.point}

## 問題例

{args.prompt or 'この内容について、最も適切な説明を選ぶ。'}

## 補足

{args.note or '-'}
"""
    path.write_text(text, encoding="utf-8")
    command_rebuild(argparse.Namespace(root=str(root)))
    print(f"{card_id} を作成しました: {path.relative_to(root)}")


def command_rebuild(args: argparse.Namespace) -> None:
    root = root_path(args.root)
    ensure_layout(root)
    cards = load_cards(root)
    today = study_day()
    rows = ["# 復習カード一覧", "", "`カード本文/` のMarkdownカードを正本とする一覧です。", "", "| Card ID | 分野 | 内容 | 次回復習 | 段階 | 直近評価 |", "|---|---|---|---|---:|---|"]
    for card in cards:
        rows.append(f"| [{card.card_id}](カード本文/{card.path.name}) | {card.domain} | {card.title} | {card.next_review} | {card.stage} | {card.last_result} |")
    if not cards:
        rows.append("| - | - | まだカードはありません | - | - | - |")
    (root / "復習カード" / "カード一覧.md").write_text("\n".join(rows) + "\n", encoding="utf-8")

    domains: dict[str, list[Card]] = {}
    for card in cards:
        domains.setdefault(card.domain, []).append(card)
    report = ["# 分野別状況", "", f"基準日: {today.isoformat()}（JST、午前5時切替）", "", "| 分野 | カード数 | 期限超過 | 今日の対象 | 誤答・不明 |", "|---|---:|---:|---:|---:|"]
    for domain, items in sorted(domains.items()):
        due = [item for item in items if date.fromisoformat(item.next_review) < today]
        today_items = [item for item in items if item.next_review == today.isoformat()]
        weak = [item for item in items if item.last_result in {"incorrect", "unknown"}]
        report.append(f"| {domain} | {len(items)} | {len(due)} | {len(today_items)} | {len(weak)} |")
    if not domains:
        report.append("| - | 0 | 0 | 0 | 0 |")
    (root / "進捗" / "分野別状況.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    print("カード一覧と分野別状況を更新しました。")


def priority(card: Card, today: date) -> tuple[int, int, int, str]:
    next_day = date.fromisoformat(card.next_review)
    overdue = (today - next_day).days
    if card.last_result == "unknown":
        bucket = 0
    elif card.last_result == "incorrect":
        bucket = 1
    elif overdue > 0:
        bucket = 2
    elif overdue == 0:
        bucket = 3
    else:
        bucket = 4
    return (bucket, -overdue, card.stage, card.card_id)


def command_candidates(args: argparse.Namespace) -> None:
    root = root_path(args.root)
    cards = load_cards(root)
    if args.domain:
        cards = [card for card in cards if card.domain == args.domain]
    if not cards:
        print("候補カードはありません。先に学んだことを登録してください。")
        return
    today = study_day()
    selected = sorted(cards, key=lambda card: priority(card, today))[: args.count]
    print("| 優先 | Card ID | 分野 | 内容 | 次回復習 | 直近評価 |")
    print("|---:|---|---|---|---|---|")
    for index, card in enumerate(selected, 1):
        print(f"| {index} | {card.card_id} | {card.domain} | {card.title} | {card.next_review} | {card.last_result} |")


def question_sections(text: str) -> dict[int, str]:
    matches = list(re.finditer(r"^### Q(\d+)\n", text, re.MULTILINE))
    return {int(match.group(1)): text[match.end(): matches[index + 1].start() if index + 1 < len(matches) else len(text)] for index, match in enumerate(matches)}


def parse_key(path: Path) -> dict[int, tuple[str, str]]:
    result = {}
    for number, body in question_sections(path.read_text(encoding="utf-8")).items():
        correct = re.search(r"^- Correct: ([A-D])$", body, re.MULTILINE)
        explanation = re.search(r"^- Explanation: (.+)$", body, re.MULTILINE)
        if not correct or not explanation:
            raise ValueError(f"{path}: Q{number} の Correct または Explanation がありません")
        result[number] = (correct.group(1), explanation.group(1))
    return result


def command_grade(args: argparse.Namespace) -> None:
    root = root_path(args.root)
    session_path = (root / args.session).resolve()
    key_path = (root / args.key).resolve()
    if not session_path.is_file() or not key_path.is_file():
        raise ValueError("Sessionまたは解答キーが見つかりません")
    session = session_path.read_text(encoding="utf-8")
    if re.search(r"^- Status: graded$", session, re.MULTILINE):
        raise ValueError("このSessionはすでに採点済みです")
    keys = parse_key(key_path)
    cards = {card.card_id: card for card in load_cards(root)}
    today = study_day()
    additions: dict[int, str] = {}
    results: list[tuple[int, str, str]] = []
    for number, body in question_sections(session).items():
        card_id = re.search(r"^- Card ID: (.+)$", body, re.MULTILINE)
        selected = re.findall(r"^- \[x\] ([A-E])\. ", body, re.MULTILINE)
        if not card_id or number not in keys:
            raise ValueError(f"Q{number} の Card ID または解答キーがありません")
        card = cards.get(card_id.group(1).strip())
        if not card:
            raise ValueError(f"Q{number}: カード {card_id.group(1).strip()} が見つかりません")
        correct, explanation = keys[number]
        if len(selected) != 1 or selected[0] == "E":
            result, score, stage, next_day = "unknown", 0, 0, today + timedelta(days=1)
        elif selected[0] == correct:
            stage = card.stage + 1
            interval = INTERVALS[min(stage, len(INTERVALS) - 1)]
            result, score, next_day = "correct", 100, today + timedelta(days=interval)
        else:
            result, score, stage, next_day = "incorrect", 0, 0, today + timedelta(days=1)
        write_card(card, card.path.read_text(encoding="utf-8"), stage=stage, result=result, reviewed=today, next_date=next_day)
        additions[number] = f"\n### 採点\n\nScore: {score} / 100\nResult: {result}\nNext Review: {next_day.isoformat()}\n\n#### 解説\n\n正解: {correct}\n\n{explanation}\n"
        results.append((number, result, next_day.isoformat()))
    for number in sorted(additions, reverse=True):
        pattern = rf"(^### Q{number}\n.*?)(?=^### Q|\Z)"
        session = re.sub(pattern, lambda match: match.group(1).rstrip() + additions[number] + "\n", session, count=1, flags=re.MULTILINE | re.DOTALL)
    session = re.sub(r"^- Status: awaiting_answers$", "- Status: graded", session, count=1, flags=re.MULTILINE)
    session_path.write_text(session, encoding="utf-8")
    history = root / "学習記録" / "学習履歴.md"
    with history.open("a", encoding="utf-8") as handle:
        handle.write(f"\n## {today.isoformat()}\n\n- [{session_path.stem}]({session_path.relative_to(history.parent).as_posix()}): " + ", ".join(f"Q{number} {result} → {next_day}" for number, result, next_day in results) + "\n")
    command_rebuild(argparse.Namespace(root=str(root)))
    print(f"{session_path.relative_to(root)} を採点しました。")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="A-1適応復習のMarkdown管理")
    result.add_argument("--root", default=".", help="学習リポジトリのルート")
    commands = result.add_subparsers(dest="command", required=True)
    commands.add_parser("init")
    add = commands.add_parser("add-card")
    add.add_argument("--domain", required=True)
    add.add_argument("--title", required=True)
    add.add_argument("--point", required=True)
    add.add_argument("--source", required=True)
    add.add_argument("--related-domains", default="")
    add.add_argument("--prompt", default="")
    add.add_argument("--note", default="")
    rebuild = commands.add_parser("rebuild")
    candidates = commands.add_parser("candidates")
    candidates.add_argument("--count", type=int, default=5)
    candidates.add_argument("--domain")
    grade = commands.add_parser("grade")
    grade.add_argument("--session", required=True)
    grade.add_argument("--key", required=True)
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        if args.command == "init":
            command_init(args)
        elif args.command == "add-card":
            command_add_card(args)
        elif args.command == "rebuild":
            command_rebuild(args)
        elif args.command == "candidates":
            command_candidates(args)
        elif args.command == "grade":
            command_grade(args)
    except (ValueError, OSError) as error:
        print(f"エラー: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
