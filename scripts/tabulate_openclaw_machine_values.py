#!/usr/bin/env python3
"""Tabulate which machine numbers stay in the public OpenClaw inventories and receipts.

Reads ``inputs/capture/state-before.json``, ``r2-state-after.json`` and ``r2-native-receipt.json`` of each packet of a
public set and counts, per kind of entry, the entries whose number is kept. A number counts as blanked when it is a 1
followed by zeros (the sanitizer keeps the digit count); ``entry_count`` is never blanked. ``tests/test_openclaw_public_sanitization.py`` checks that the
table in ``docs/survival-v1/adapters/openclaw.md`` is this output.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import sys

INVENTORIES = ('state-before.json', 'r2-state-after.json')
FIELDS = ('device', 'inode', 'size_bytes', 'entry_count', 'mtime_ns', 'ctime_ns', 'birth_ns')
KINDS = ('digested root, in a receipt class', 'digested root, in no receipt class', 'directory in a receipt class',
         'directory above a classified entry, in no class', 'link in a receipt class', 'link in no class', 'file in a receipt class',
         'session-store file in no class', 'every other unclassified entry')
_BLANK = re.compile(r'10*')


def kept(value, field: str = '') -> bool:
    """The sanitizer never blanks ``entry_count`` (a count of 1 or 10 looks like a blank): it counts as kept when it is not 0."""
    return value != 0 if field == 'entry_count' else not _BLANK.fullmatch(str(value))


def classify(inventory: dict, receipt: dict) -> list[tuple[str, dict]]:
    classes = receipt['classes']
    classified = set(classes['session_owned']) | set(classes['run_owned']) | set(classes['directories_changed']) | {row['relative_path'] for row in classes['shared_changed']}
    stores = {store + suffix for store in receipt['rules']['session_stores'] for suffix in ('', '-wal', '-shm', '-journal')}
    anchors = classified | {entry['relative_path'] for entry in inventory['entries'] if entry['kind'] == 'directory-digest'} | stores
    above = {'/'.join(path.split('/')[:depth]) for path in anchors for depth in range(1, len(path.split('/')))}
    rows = []
    for entry in inventory['entries']:
        path, kind, in_class = entry['relative_path'], entry['kind'], entry['relative_path'] in classified
        if kind == 'directory-digest':
            name = KINDS[0] if in_class else KINDS[1]
        elif kind == 'directory':
            name = KINDS[2] if in_class else KINDS[3] if path in above else KINDS[8]
        elif kind == 'symlink':
            name = KINDS[4] if in_class else KINDS[5]
        elif kind == 'file':
            name = KINDS[6] if in_class else KINDS[7] if path in stores else KINDS[8]
        else:
            raise ValueError('unknown entry kind ' + kind)
        rows.append((name, entry))
    return rows


def _cell(counts: list[tuple[int, int]]) -> str:
    """``none``, ``all`` or ``k of n``; the numbers of the six inventories (three runs, before and after) when they differ."""
    if all(k == 0 for k, _ in counts):
        return 'none'
    if all(k == n for k, n in counts):
        return 'all'
    return ' / '.join(f'{k} of {n}' for k, n in counts) if len(set(counts)) > 1 else f'{counts[0][0]} of {counts[0][1]}'


def tables(public: Path) -> str:
    packets = sorted(path for path in Path(public).iterdir() if (path / 'manifest.json').is_file())
    per_kind: dict[str, list[list[int]]] = {kind: [] for kind in KINDS}
    shared: dict[str, list[dict]] = {}
    for packet in packets:
        capture = packet / 'inputs/capture'
        receipt = json.loads((capture / 'r2-native-receipt.json').read_bytes())
        for name in INVENTORIES:
            inventory = json.loads((capture / name).read_bytes())
            totals = {kind: {field: [0, 0] for field in ('entries', *FIELDS)} for kind in KINDS}
            for kind, entry in classify(inventory, receipt):
                totals[kind]['entries'][1] += 1
                for field in FIELDS:
                    if field in entry:
                        totals[kind][field][1] += 1
                        totals[kind][field][0] += kept(entry[field], field)
            for kind in KINDS:
                per_kind[kind].append(totals[kind])
        kinds = {row['relative_path']: row['kind'] for row in receipt['classes']['shared_changed']}
        for row in receipt['classes']['shared_changed']:
            for side in ('before', 'after'):
                if isinstance(row.get(side), dict):
                    shared.setdefault(row['kind'], []).append({'run': packet.name, 'side': side, **row[side]})
    lines = ['| Entry in the inventory | Entries | ' + ' | '.join(f'`{field}`' for field in FIELDS) + ' |', '|---|---|' + '---|' * len(FIELDS)]
    for kind in KINDS:
        entries = [t['entries'][1] for t in per_kind[kind]]
        count = str(entries[0]) if len(set(entries)) == 1 else '/'.join(map(str, entries))
        cells = []
        for field in FIELDS:
            pairs = [tuple(t[field]) for t in per_kind[kind]]
            cells.append('n/a' if all(n == 0 for _, n in pairs) else _cell(pairs))
        lines.append(f'| {kind} | {count} | ' + ' | '.join(cells) + ' |')
    receipt_lines = ['| Receipt row (`classes.shared_changed`, before and after) | Values | `size_bytes` | `entry_count` |', '|---|---|---|---|']
    for kind in sorted(shared):
        values = shared[kind]
        sizes = [(sum(kept(v['size_bytes']) for v in values if v['run'] == run and v['side'] == side), sum('size_bytes' in v for v in values if v['run'] == run and v['side'] == side))
                 for run in sorted({v['run'] for v in values}) for side in ('before', 'after')]
        counts = [(sum(kept(v['entry_count'], 'entry_count') for v in values if v['run'] == run and v['side'] == side and 'entry_count' in v), sum('entry_count' in v for v in values if v['run'] == run and v['side'] == side))
                  for run in sorted({v['run'] for v in values}) for side in ('before', 'after')]
        total = [sum('size_bytes' in v for v in values if v['run'] == run and v['side'] == side) for run in sorted({v['run'] for v in values}) for side in ('before', 'after')]
        receipt_lines.append(f'| {kind} | ' + '/'.join(map(str, total)) + ' | ' + ('n/a' if all(n == 0 for _, n in sizes) else _cell(sizes)) + ' | '
                             + ('n/a' if all(n == 0 for _, n in counts) else _cell(counts)) + ' |')
    return '\n'.join(lines) + '\n\n' + '\n'.join(receipt_lines) + '\n'


if __name__ == '__main__':
    sys.stdout.write(tables(Path(sys.argv[1])))
