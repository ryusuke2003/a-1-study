#!/usr/bin/env python3
"""Compare documented per-task reading bytes/chars; not runtime tokens or latency.

Run after applying the patch:
  python3 scripts/measure_skill_context.py --base-ref 84e1dc53e0719b99593d629634424e9128de17a5
For an offline, hash-verified source subset, use --baseline-dir instead.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess

PREFIX = 'skills/a1-adaptive-review/'
ENTRY = PREFIX + 'SKILL.md'
FORMAT = PREFIX + 'references/問題・採点形式.md'
MANIFEST = PREFIX + 'references/採点マニフェスト.md'
BEFORE = {
    'status': [ENTRY],
    'register': [ENTRY],
    'generate': [ENTRY, FORMAT],
    'grade': [ENTRY, FORMAT, MANIFEST],
}
AFTER = {
    'status': [ENTRY],
    'register': [ENTRY, PREFIX + 'references/登録・整理.md'],
    'generate': [ENTRY, PREFIX + 'references/出題手順.md', FORMAT],
    'grade': [ENTRY, MANIFEST, PREFIX + 'references/採点・誤答形式.md'],
}


def measure(chunks: list[bytes]) -> dict[str, int]:
    return {'utf8_bytes': sum(len(chunk) for chunk in chunks),
            'unicode_characters': sum(len(chunk.decode('utf-8')) for chunk in chunks)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline-layout', choices=('auto', 'legacy', 'routed'), default='auto',
                        help='Layout of baseline instructions; auto detects the routed entrypoint')
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--base-ref')
    group.add_argument('--baseline-dir', type=Path)
    args = parser.parse_args()
    root = args.root.resolve()

    def before(path: str) -> bytes:
        if args.baseline_dir:
            return (args.baseline_dir / path).read_bytes()
        return subprocess.check_output(['git', 'show', f'{args.base_ref}:{path}'], cwd=root)

    layout = args.baseline_layout
    if layout == 'auto':
        layout = 'routed' if all(path.removeprefix(PREFIX) in before(ENTRY).decode('utf-8')
                                 for path in AFTER['register'][1:]) else 'legacy'
    baseline_tasks = AFTER if layout == 'routed' else BEFORE

    output = {'baseline_layout': layout, 'method': 'Unique required instruction files per task; whole files, including frontmatter. '
                        'Excludes AGENTS, README, study data, script source, tool output and chat history. '
                        'Measures documented loading, not observed model usage.',
              'tasks': {}}
    for task in BEFORE:
        old = measure([before(path) for path in baseline_tasks[task]])
        new = measure([(root / path).read_bytes() for path in AFTER[task]])
        reduction = {key: round(100 * (1 - new[key] / old[key]), 2) for key in old}
        output['tasks'][task] = {'before': old, 'after': new, 'reduction_percent': reduction,
                                 'before_paths': baseline_tasks[task], 'after_paths': AFTER[task]}
    before_docs = list(dict.fromkeys(path for paths in baseline_tasks.values() for path in paths))
    after_docs = sorted((root / PREFIX).rglob('*.md'))
    output['all_instruction_files'] = {
        'before': measure([before(path) for path in before_docs]),
        'after': measure([path.read_bytes() for path in after_docs]),
        'note': 'Total stored instructions and per-task reading sets are separate metrics.',
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
