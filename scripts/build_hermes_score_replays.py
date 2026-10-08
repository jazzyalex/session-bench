#!/usr/bin/env python3
"""Build the three private Hermes score replay packets from the whole-home stream captures.

The native record of a Hermes session is its rows in the shared store
``~/.hermes/state.db``. Each packet holds the row export and the schema export
of the second turn (``r2-native/``), the system prompt row of the session, the
independent capture documents (the submitted prompts, the ``stream-json``
stdout of each turn, the helper ledger and the workspace copies), the
whole-home receipt of the second turn with its two metadata inventories, and
the version extract. The observer is built from the stdout streams. Every
packet is replayed under the OS sandbox and passes the tamper controls. The
packets are private.

The exporter file of ``hermes sessions export`` is a derived projection. It is
not in the packets. The builder compares it with the rows and writes the
result to the private summary (``exporter_cross_check``).

Two reads were made after the captures, on the owner's decision, each from a
private copy of the store that was deleted. ``--version-extract`` is the one
row of the store table ``schema_version``; it is bound into each packet as a
capture document and must name the schema digest of the capture. The
``system_prompts`` row of each session
(``<capture>/shared-store-extract/system-prompt-row.json``) joins the native
family; it must be the row whose hash the captured session row names.

Designated runs: the first three captures that the corrected controller
completed (``hermes-codex-2026-10-07-08``, ``-09``, ``-10``; ``chat -q
--format stream-json`` with the terminal and file toolsets). Decided before
scoring. Disclosed and not used: ``-07`` (stopped before any model request;
launcher entry-code fault), ``2026-10-06-03`` (stopped; controller rule gap),
``2026-10-06-04`` to ``-06`` (terminal toolset only and no tool stream; the set
built from them was rejected), and the two exporter-only captures.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from session_bench.hermes_score_inputs import (
    AFTER, BEFORE, RECEIPT, STREAM_ASSERTION_SCHEMA, STREAM_CAPTURE_DOCUMENTS, STREAM_OBSERVER_KIND, VERSION_EXTRACT, build_of,
    required_companions, state_root_row, stream_observer_from_capture_documents,
)
from session_bench.hermes_state_evidence import verify_hermes_state_capture
from session_bench.hermes_store_rows import PROMPT_FILE, ROWS_FILE, SCHEMA_FILE, _tool_calls, read_rows
from session_bench.native_replay import canonical
from session_bench.score_replay import (
    SCHEMA, build_score_replay_package, replay_score_package, verify_score_packet_tamper_controls,
)

RUNS = ('hermes-codex-2026-10-07-08', 'hermes-codex-2026-10-07-09', 'hermes-codex-2026-10-07-10')
CAPTURES = ROOT / 'artifacts/v1-expanded-preparation/live-captures'
HELPER = ROOT / 'fixtures/scenarios/survival-v1/workload/fixture_project/bench_check.py'
# Read after the captures on the owner's decision (2026-10-06), each from a private copy of the store that was deleted:
# the one row of the store table schema_version, and per capture the system_prompts row that its session row names.
PROMPT_EXTRACT = 'shared-store-extract/system-prompt-row.json'
RESOLVED = ('measured', 'native_absent', 'contradiction')


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def capture_documents(capture: Path, version_extract: Path | None = None) -> dict[str, bytes]:
    """The capture documents of one run, after the offline checks of both whole-home receipts."""
    if version_extract is None:
        raise ValueError('the version extract is required (--version-extract)')
    documents = {name: (capture / name).read_bytes() for name in STREAM_CAPTURE_DOCUMENTS if name != VERSION_EXTRACT}
    if documents['turn-r2/workspace/fixture_project/bench_check.py'] != HELPER.read_bytes():
        raise ValueError('captured helper differs from the frozen helper')
    before = json.loads(documents[BEFORE])
    sessions = set()
    for turn in (1, 2):
        # Each receipt is checked against its inventories, its row export and the deleted store copy.
        receipt = json.loads((capture / f'r{turn}-native-receipt.json').read_bytes())
        verify_hermes_state_capture(receipt, capture / f'r{turn}-native', before=before,
                                    after=json.loads((capture / f'r{turn}-state-after.json').read_bytes()))
        sessions.add(receipt['session_id'])
    if len(sessions) != 1:
        raise ValueError('the two turns used different sessions')
    documents[VERSION_EXTRACT] = canonical(json.loads(Path(version_extract).read_bytes()))
    return documents


def exporter_cross_check(capture: Path) -> dict:
    """Compare the derived exporter file with the rows (private; the exporter file is outside the read)."""
    rows, schema = ((capture / 'r2-native' / name).read_bytes() for name in (ROWS_FILE, SCHEMA_FILE))
    tables, _, _ = read_rows(rows, schema)
    exported = json.loads((capture / 'turn-r2/native/session.jsonl').read_bytes().splitlines()[0])
    native, derived = [row['values'] for row in tables['messages']], exported.get('messages', [])
    session = tables['sessions'][0]['values']
    differences = []
    if len(native) != len(derived):
        differences.append(f'message count: rows {len(native)}, exporter {len(derived)}')
    for index, (row, item) in enumerate(zip(native, derived)):
        if row['role'] != item.get('role') or (row['content'] or '') != (item.get('content') or ''):
            differences.append(f'message {index}: role or content differs')
        ours = [call for call, _, _ in _tool_calls(row) or []]
        theirs = [call.get('id') for call in item.get('tool_calls') or [] if isinstance(call, dict)]
        if ours != theirs or row.get('tool_call_id') != item.get('tool_call_id'):
            differences.append(f'message {index}: tool call ids differ')
    message_keys = sorted({key for item in derived for key in item})
    return {'exporter_file': 'turn-r2/native/session.jsonl', 'role': 'derived projection; outside the read',
            'session_id_equal': exported.get('id', exported.get('session_id')) == session['id'],
            'message_rows': len(native), 'exporter_messages': len(derived), 'differences': differences,
            'exporter_session_keys': sorted(key for key in exported if key != 'messages'), 'exporter_message_keys': message_keys,
            'row_columns_not_in_exporter_messages': sorted(set(tables['messages'][0]['values']) - set(message_keys)) if native else [],
            'session_columns_not_in_exporter': sorted(set(session) - set(exported)),
            'exporter_keys_not_in_rows': {'session': sorted(set(exported) - set(session) - {'messages'}),
                                          'message': sorted(set(message_keys) - set(tables['messages'][0]['values'])) if native else []}}


def build(output: Path, *, captures: Path = CAPTURES, runs=RUNS, version_extract: Path | None = None, os_sandboxed: bool = True) -> dict:
    output = Path(output)
    if output.exists() or output.is_symlink():
        raise ValueError('new destination required')
    output.mkdir(parents=True)
    summary = []
    for repetition, run in enumerate(runs, 1):
        capture = captures / run
        documents = capture_documents(capture, version_extract)
        plan = json.loads(documents['plan.json'])
        if plan['repetition'] != repetition or plan['attempt_id'] != run:
            raise ValueError('capture is not the designated repetition')
        native_documents = {'capture/' + name: (capture / 'r2-native' / name).read_bytes() for name in (ROWS_FILE, SCHEMA_FILE)}
        # The system prompt row of the session, stored in canonical form. The packet validator binds it by content.
        native_documents['capture/' + PROMPT_FILE] = canonical(json.loads((capture / PROMPT_EXTRACT).read_bytes())) + b'\n'
        tables, _, _ = read_rows(native_documents['capture/' + ROWS_FILE], native_documents['capture/' + SCHEMA_FILE])
        observer_bytes = canonical(stream_observer_from_capture_documents(documents))
        # The observation date is the UTC date of the native session start (sessions.started_at).
        collected_on = datetime.fromtimestamp(tables['sessions'][0]['values']['started_at'], tz=timezone.utc).date().isoformat()
        inventory = {'artifacts': [{'path': name, 'sha256': sha(data), 'size_bytes': len(data)} for name, data in sorted(native_documents.items())]}
        inventory_bytes = canonical(inventory) + b'\n'
        assertion = {'schema_version': STREAM_ASSERTION_SCHEMA, 'run_id': run, 'repetition': repetition,
                     'native_inventory_sha256': sha(inventory_bytes),
                     'input_sha256': {'workload.json': sha(documents['workload-instance.json']), 'observer.json': sha(observer_bytes)},
                     'capture_documents': [{'path': name, 'sha256': sha(data), 'size_bytes': len(data)} for name, data in sorted(documents.items())]}
        supporting = {'capture-assertion.json': canonical(assertion) + b'\n'}
        supporting.update({'capture/' + name: data for name, data in documents.items()})
        context = {'schema_version': SCHEMA, 'configuration_id': 'hermes', 'repetition': repetition, 'run_id': run,
                   'build': build_of(json.loads(documents['preflight.json'])['version']), 'collected_on': collected_on,
                   'result_id': run + '-native-score-replay', 'observer_kind': STREAM_OBSERVER_KIND,
                   # The store holds other sessions and cannot be copied whole: the family is complete, the root is not.
                   'complete_record_family': True, 'complete_root': False,
                   'required_companions': required_companions(tables),
                   'root_repetitions': [state_root_row(repetition)],
                   'capture_assertion_path': 'inputs/capture-assertion.json', 'claude_projection': None}
        with tempfile.TemporaryDirectory(prefix='bench-hermes-native-') as directory:
            native = Path(directory).resolve()
            (native / 'decode.json').write_bytes(inventory_bytes)
            for name, data in native_documents.items():
                target = native / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            packet = output / run
            manifest = build_score_replay_package(native, packet, workload_document=documents['workload-instance.json'],
                observer_document=observer_bytes, context_document=canonical(context), supporting_documents=supporting)
        pinned = sha((packet / 'manifest.json').read_bytes())
        replayed = replay_score_package(packet, expected_manifest_sha256=pinned, os_sandboxed=os_sandboxed)
        controls = verify_score_packet_tamper_controls(packet, expected_manifest_sha256=pinned)
        if controls['status'] != 'passed':
            raise ValueError('Hermes packet tamper controls failed')
        (output / f'{run}-receipt.json').write_bytes(canonical(replayed) + b'\n')
        (output / f'{run}-tamper.json').write_bytes(canonical(controls) + b'\n')
        metrics = replayed['diagnostics']['intact']['metrics']
        unresolved = [row['id'] for row in metrics if row['state'] not in RESOLVED]
        summary.append({'run_id': run, 'repetition': repetition, 'packet': packet.name,
                        'manifest_sha256': pinned, 'diagnostics_sha256': manifest['expected_diagnostics_sha256'],
                        'metric_count': len(metrics), 'resolved_metric_count': len(metrics) - len(unresolved),
                        'unresolved_metric_ids': unresolved, 'os_sandboxed': replayed['os_sandboxed'],
                        'tamper_controls': controls['status'],
                        'selected_loss_detected': replayed['diagnostics']['selected_loss']['response_correctness_reduced'],
                        'exporter_cross_check': exporter_cross_check(capture)})
    result = {'schema_version': 'session-bench-hermes-score-replay-v1', 'scope': 'private_native_to_score_diagnostics',
              'public_safe': False, 'independent_reproduction': False, 'overall_rank': None, 'runs': summary}
    (output / 'summary.json').write_bytes(canonical(result) + b'\n')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--version-extract', type=Path, default=None,
                        help='the bound row of the store table schema_version (scripts/extract_hermes_store_version.py)')
    arguments = parser.parse_args()
    print(json.dumps(build(arguments.output, version_extract=arguments.version_extract), indent=2, sort_keys=True))
