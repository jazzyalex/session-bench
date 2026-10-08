"""Closed private Antigravity replay inputs from retained capture bytes.

Two capture kinds exist. ``antigravity-capture-v1`` is the retained
transcript-only qualification: family and root stay unproved and the broad
rows stay unresolved. ``antigravity-capture-v2`` is a capture of agy 1.2.16
with a session-root receipt. The receipt holds metadata-only inventories of
the normal ``brain`` root before the first turn and after the second turn. It
proves one new session directory, no other change in that root, a quiet root
before the copy, and an equal copy of every file of the directory. Only then
are the family and the root complete. Nothing outside ``brain`` was listed or
read; the receipt says so (``external_companions_established`` is false).
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
import re

from .antigravity_live import EDIT_TOOLS, decode_antigravity_family, decode_antigravity_native, stdout_projection
from .live_observer import build_opencode_live_observer
from .native_replay import canonical
from .v1_public_score import CLASSIFIED_CONTENT_DENSITY_RULE

ASSERTION_SCHEMA = 'session-bench-antigravity-score-inputs-v1'
ASSERTION_SCHEMA_V2 = 'session-bench-antigravity-score-inputs-v2'
OBSERVER_KIND_V2 = 'antigravity-capture-v2'
ROOT_SUFFIX = '/.gemini/antigravity-cli/brain'
ROOT_LOCATOR = ('~/.gemini/antigravity-cli/brain/<conversation-id>/ (primary .system_generated/logs/transcript.jsonl; '
                'the one new top-level directory between two metadata-only inventories of brain/)')
CAPTURE_DOCUMENTS_V2 = ('workload.json', 'capture-start.json', 'capture-result.json', 'r1.stdout.jsonl', 'r2.stdout.jsonl',
                        'r1.stderr.txt', 'r2.stderr.txt', 'r2-native-receipt.json', 'helper-ledger.jsonl', 'bench_check.py',
                        'workspace/fixture_project/checkout.py')
TARGET = 'fixture_project/checkout.py'


def _bounded_session_files(before: dict, after: dict, primary: str):
    if (before.get('metadata_only') is not True or after.get('metadata_only') is not True
            or before.get('root') != after.get('root')
            or before.get('root_identity') != after.get('root_identity')):
        raise ValueError('Antigravity bounded root identity changed')
    def indexed(snapshot):
        result = {}
        for item in snapshot.get('entries', []):
            if not isinstance(item, dict):
                raise ValueError('Antigravity bounded root entry invalid')
            name = item.get('relative_path')
            if (not isinstance(name, str) or not name or name.startswith('/')
                    or '\\' in name or any(part in ('', '.', '..') for part in name.split('/'))
                    or name in result or item.get('kind') not in ('file', 'directory')):
                raise ValueError('Antigravity bounded root path invalid')
            result[name] = item
        return result
    old, new = indexed(before), indexed(after)
    parts = primary.split('/') if isinstance(primary, str) else []
    if len(parts) != 4 or parts[1:] != ['.system_generated', 'logs', 'transcript.jsonl']:
        raise ValueError('Antigravity bounded root primary invalid')
    session = parts[0]
    if (session in old or any(path.startswith(session + '/') for path in old)
            or new.get(session, {}).get('kind') != 'directory'
            or new.get(primary, {}).get('kind') != 'file'):
        raise ValueError('Antigravity bounded root session invalid')
    if any(new.get(path) != item for path, item in old.items()):
        raise ValueError('Antigravity bounded root old entry changed')
    if any(path != session and not path.startswith(session + '/') for path in new.keys() - old.keys()):
        raise ValueError('Antigravity bounded root outside entry changed')
    files = {path: item for path, item in new.items() if path.startswith(session + '/') and item['kind'] == 'file'}
    identities = [(item.get('device'), item.get('inode')) for item in files.values()]
    if (not files or len(set(identities)) != len(identities)
            or any(identity in {(item.get('device'), item.get('inode')) for item in old.values()} for identity in identities)):
        raise ValueError('Antigravity bounded root file identity invalid')
    return session, files, new[session]


def validate_bounded_root_receipt(receipt: dict, documents: dict[str, bytes],
                                  native_inventory: dict, *, run_id: str, primary_path: str) -> bool:
    """Bind a new-session receipt without claiming the external native family."""
    if receipt.get('schema_version') != 'antigravity-session-root-v1':
        return False
    if (receipt.get('attempt_id') != run_id or receipt.get('turn') != 2
            or receipt.get('primary_path') != primary_path
            or receipt.get('scope') != 'complete_new_session_directory_within_inventoried_brain_root'
            or receipt.get('external_companions_established') is not False
            or receipt.get('metadata_only_before_after') is not True
            or receipt.get('preexisting_contents_opened') is not False):
        raise ValueError('Antigravity bounded root receipt overclaims or differs')
    before, after = receipt.get('before_inventory'), receipt.get('after_inventory')
    if not isinstance(before, dict) or not isinstance(after, dict):
        raise ValueError('Antigravity bounded root inventories missing')
    session, files, directory = _bounded_session_files(before, after, primary_path)
    if (receipt.get('source_root') != before.get('root')
            or receipt.get('session_directory') != session
            or receipt.get('session_identity') != {'device': directory['device'], 'inode': directory['inode']}):
        raise ValueError('Antigravity bounded root identity differs')
    quiet = receipt.get('quiescence')
    if (not isinstance(quiet, dict) or quiet.get('stable') is not True
            or type(quiet.get('checks')) is not int or quiet['checks'] < 2
            or quiet.get('observed') != [after] * quiet['checks']):
        raise ValueError('Antigravity bounded root quiescence invalid')
    artifacts = receipt.get('artifacts')
    expected = files
    if (not isinstance(artifacts, list) or len(artifacts) != len(expected)
            or {item.get('relative_path') for item in artifacts if isinstance(item, dict)} != set(expected)):
        raise ValueError('Antigravity bounded root copied family differs')
    declared = {entry['path'] for entry in native_inventory['artifacts']}
    if declared != {'capture/' + name for name in expected}:
        raise ValueError('Antigravity bounded root native inventory differs')
    for item in artifacts:
        name = item['relative_path']
        source = expected[name]
        data = documents.get('native/' + name)
        role = 'native-primary' if name == primary_path else 'native-companion'
        if (data is None or item.get('role') != role
                or item.get('filesystem_id') != f"{source['device']}:{source['inode']}"
                or item.get('size_bytes') != source['size_bytes']
                or item.get('sha256') != sha(data) or len(data) != source['size_bytes']):
            raise ValueError('Antigravity bounded root member differs')
    return True


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_json(data: bytes, label: str):
    def pairs(items):
        out = {}
        for key, value in items:
            if key in out:
                raise ValueError(f'{label}: duplicate JSON key')
            out[key] = value
        return out
    return json.loads(data, object_pairs_hook=pairs,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError(f'{label}: non-finite {value}')))


def _bound_final_response_usage(stdout: bytes, *, expected_response: str, expected_session_id: str) -> dict:
    """Project only the unique DONE usage on the exact streamed final step."""
    rows = [(number, read_json(line, 'Antigravity stdout')) for number, line in
            enumerate(stdout.splitlines(), 1) if line.strip()]
    initial = [row for _, row in rows if row.get('event') == 'init']
    results = [(number, row['result']) for number, row in rows if row.get('event') == 'result']
    if (len(initial) != 1 or initial[0].get('conversation_id') != expected_session_id
            or len(results) != 1 or results[0][1].get('conversation_id') != expected_session_id
            or results[0][1].get('status') != 'SUCCESS'
            or results[0][1].get('response') != expected_response):
        raise ValueError('Antigravity final response boundary differs')
    result_line = results[0][0]
    steps = {}
    for number, row in rows:
        step = row.get('step_update')
        if not isinstance(step, dict):
            continue
        if step.get('conversation_id') != expected_session_id or type(step.get('step_index')) is not int:
            raise ValueError('Antigravity response step identity differs')
        index = step['step_index']
        item = steps.setdefault(index, {'type': step.get('step_type'), 'deltas': [], 'done': []})
        if item['type'] != step.get('step_type'):
            raise ValueError('Antigravity response step type changed')
        if 'text_delta' in step:
            if item['type'] != 'agent_response' or not isinstance(step['text_delta'], str):
                raise ValueError('Antigravity response delta invalid')
            item['deltas'].append((number, step['text_delta']))
        if step.get('state') == 'DONE':
            item['done'].append((number, step))
    streamed = [(index, item) for index, item in steps.items() if item['deltas']]
    if len(streamed) != 1:
        raise ValueError('Antigravity streamed final response ambiguous')
    index, final = streamed[0]
    if ''.join(value for _, value in final['deltas']) != expected_response or len(final['done']) != 1:
        raise ValueError('Antigravity final response step differs')
    usage_line, completion = final['done'][0]
    if usage_line < final['deltas'][-1][0] or usage_line >= result_line:
        raise ValueError('Antigravity final response order differs')
    usage = completion.get('usage')
    required = ('input_tokens', 'output_tokens', 'thinking_tokens', 'cache_read_tokens', 'total_tokens')
    if (not isinstance(usage, dict)
            or any(type(usage.get(key)) is not int or usage[key] < 0 for key in required)
            or any(type(value) is not int or value < 0 for value in usage.values())
            or usage['total_tokens'] != usage['input_tokens'] + usage['output_tokens']):
        raise ValueError('Antigravity final response usage invalid')
    return {'schema_version': 'session-bench-antigravity-final-response-usage-v1',
            'source_stdout_sha256': sha(stdout), 'source_size_bytes': len(stdout),
            'session_id': expected_session_id, 'response_sha256': sha(expected_response.encode()),
            'response_step_index': index, 'response_delta_lines': [line for line, _ in final['deltas']],
            'usage_line': usage_line, 'result_line': result_line, 'usage': dict(usage),
            'scope': 'final_displayed_response_step_only'}


def _steps_without_stream_output(stdout: bytes) -> set[int]:
    """Step indexes of completed tool steps for which the stream shows no output."""
    missing = set()
    for line in stdout.splitlines():
        if not line.strip():
            continue
        step = read_json(line, 'Antigravity stdout').get('step_update')
        if isinstance(step, dict) and step.get('state') == 'DONE' and isinstance(step.get('tool_info'), dict) and 'output' not in step['tool_info']:
            missing.add(step.get('step_index'))
    return missing


def observer_from_capture_documents(documents: dict[str, bytes], workspace: str, *, step_bound: bool = False) -> dict:
    """Independent observer from the stdout streams, the helper ledger and the file hashes.

    With ``step_bound`` (capture kind v2) the response text is the text of the
    streamed step that holds the canary, and each result names the stream
    step it came from as ``native_result_id`` (``step:N``). The stream and the
    native transcript number the steps of one conversation with the same
    ``step_index``. A result for which the stream shows no output carries none.
    """
    workload = read_json(documents['workload.json'], 'workload')
    state = read_json(documents['capture-result.json'], 'capture result')
    ledger = documents['helper-ledger.jsonl'].decode()
    helper_rows = [read_json(line, 'helper row') for line in ledger.splitlines() if line.strip()]
    if [(row['phase'], row['exit_code']) for row in helper_rows] != [('inspect', 0), ('baseline', 1), ('final', 0)]:
        raise ValueError('Antigravity independent helper sequence incomplete')
    streams = {turn: stdout_projection(documents[f'r{turn}.stdout.jsonl'].decode(), helper_rows, step_text=step_bound) for turn in (1, 2)}
    if streams[1]['session_id'] != streams[2]['session_id'] or streams[1]['session_id'] not in state['native_primary'].split('/')[:2]:
        raise ValueError('Antigravity stdout/native session mismatch')
    observer = build_opencode_live_observer(workload=workload, controller_state={
        'turns': {str(turn): {'session_id': streams[turn]['session_id']} for turn in (1, 2)},
        'model': 'unobserved-default', 'configuration': 'antigravity-cli-default', 'workspace': workspace},
        stdout_by_turn={turn: streams[turn]['jsonl'] for turn in (1, 2)}, helper_ledger_jsonl=ledger,
        before_checkout_sha256=state['before_sha256'], after_checkout_sha256=state['after_sha256'])
    for event in observer['events']:
        for key in ('model_id', 'configuration'):
            event['fields'].pop(key, None)
        local_id = event['fields'].pop('call_id', None)
        if local_id is not None:
            event['fields']['stdout_step_id'] = local_id
    observer['method'] = ('Submitted workload, stdout steps and final response, protected helper ledger and filesystem hashes; '
                          'helper exit codes from separate ledger; model and response-scoped tokens unobserved; '
                          'completed edits are logical operations, not process exit codes.')
    if step_bound:
        silent = set().union(*(_steps_without_stream_output(documents[f'r{turn}.stdout.jsonl']) for turn in (1, 2)))
        for event in observer['events']:
            if event['kind'] != 'result':
                continue
            index = int(event['fields']['stdout_step_id'].split(':', 1)[1])
            event['fields']['native_result_id'] = f'step:{index}'
            if index in silent:
                event['fields'].pop('output', None)
        observer['method'] += (' Response text is the streamed step text without its one line terminator.'
                               ' Each result names its stream step index, which the native transcript shares.')
    return observer


def expected_root_row(repetition):
    return {'repetition': repetition, 'root_locator': ROOT_LOCATOR, 'discovery_mode': 'metadata_safe_normal_root',
            'personal_history_scanned': False}


def validate_session_root_capture(contents, context, native_inventory):
    """Capture kind v2: prove the complete new session directory in the normal root, or raise."""
    assertion = read_json(contents[context['capture_assertion_path']], 'Antigravity assertion')
    if (assertion.get('schema_version') != ASSERTION_SCHEMA_V2 or assertion.get('run_id') != context['run_id']
            or assertion.get('repetition') != context['repetition']
            or assertion.get('native_inventory_sha256') != sha(contents['native/decode.json'])):
        raise ValueError('Antigravity assertion identity/inventory mismatch')
    for name in ('workload.json', 'observer.json'):
        if assertion.get('input_sha256', {}).get(name) != sha(contents['inputs/' + name]):
            raise ValueError('Antigravity workload/observer binding mismatch')
    rows = assertion.get('capture_documents')
    if not isinstance(rows, list) or [entry.get('path') if isinstance(entry, dict) else None for entry in rows] != sorted(CAPTURE_DOCUMENTS_V2):
        raise ValueError('Antigravity capture document set differs')
    docs = {}
    for entry in rows:
        data = contents.get('inputs/capture/' + entry['path'])
        if data is None or sha(data) != entry.get('sha256') or len(data) != entry.get('size_bytes'):
            raise ValueError('Antigravity captured member changed')
        docs[entry['path']] = data
    if {name for name in contents if name.startswith('inputs/capture/')} != {'inputs/capture/' + name for name in docs}:
        raise ValueError('Antigravity capture document inventory is not closed')
    state = read_json(docs['capture-result.json'], 'capture result')
    start = read_json(docs['capture-start.json'], 'capture start')
    receipt = read_json(docs['r2-native-receipt.json'], 'native receipt')
    turns = state.get('turns')
    if (state.get('status') != 'captured_pending_qualification' or state.get('attempt_id') != context['run_id']
            or state.get('configuration_id') != 'antigravity' or state.get('version') != context['build']
            or start.get('attempt_id') != context['run_id'] or start.get('version') != state.get('version')
            or not isinstance(turns, list) or [row.get('turn') if isinstance(row, dict) else None for row in turns] != [1, 2]):
        raise ValueError('Antigravity capture completion mismatch')
    for row in turns:
        if (row.get('exit_code') != 0 or row.get('response_canary_observed') is not True
                or row.get('stdout_sha256') != sha(docs[f"r{row['turn']}.stdout.jsonl"])):
            raise ValueError('Antigravity turn receipt differs from the retained stdout')
    primary = state.get('native_primary')
    if receipt.get('schema_version') != 'antigravity-session-root-v1' or not validate_bounded_root_receipt(
            receipt, {'native/' + name[len('native/capture/'):]: data for name, data in contents.items() if name.startswith('native/capture/')},
            native_inventory, run_id=context['run_id'], primary_path=primary):
        raise ValueError('Antigravity session-root receipt missing')
    root = receipt.get('source_root')
    if not isinstance(root, str) or not root.endswith(ROOT_SUFFIX) or receipt.get('requested_model') != state.get('requested_model'):
        raise ValueError('Antigravity receipt names another root or model selection')
    if {'native/' + entry['path'] for entry in native_inventory['artifacts']} != {name for name in contents if name.startswith('native/capture/')}:
        raise ValueError('Antigravity native inventory is not closed')
    if docs['bench_check.py'] != contents['runtime/fixtures/scenarios/survival-v1/workload/fixture_project/bench_check.py']:
        raise ValueError('Antigravity helper differs from frozen source')
    if sha(docs['workspace/fixture_project/checkout.py']) != state.get('after_sha256'):
        raise ValueError('Antigravity workspace changed')
    workspace = assertion.get('captured_workspace')
    if not isinstance(workspace, str) or not workspace.endswith('/' + context['run_id'] + '/workspace'):
        raise ValueError('Antigravity workspace declaration invalid')
    if (canonical(observer_from_capture_documents(docs, workspace, step_bound=True)) != contents['inputs/observer.json']
            or docs['workload.json'] != contents['inputs/workload.json']):
        raise ValueError('Antigravity independent observer/workload differs')
    declared = sorted(entry['path'] for entry in native_inventory['artifacts'])
    if context.get('required_companions') != [name for name in declared if name != 'capture/' + primary]:
        raise ValueError('Antigravity companion set differs from the copied directory')
    if context.get('root_repetitions') != [expected_root_row(context['repetition'])]:
        raise ValueError('Antigravity root row differs from the session-root receipt')
    # The observation date is the UTC date of the first native step.
    first = read_json(contents['native/capture/' + primary].split(b'\n', 1)[0], 'Antigravity first step')
    if str(first.get('created_at'))[:10] != context['collected_on']:
        raise ValueError('Antigravity observation date differs from the first native step')
    return True, True


def validate_antigravity_capture_assertion(contents, context, native_inventory):
    if context.get('observer_kind') == 'antigravity-capture-v3':
        from .antigravity_state_inputs import validate_state_root_capture
        return validate_state_root_capture(contents, context, native_inventory)
    if context.get('observer_kind') == OBSERVER_KIND_V2:
        return validate_session_root_capture(contents, context, native_inventory)
    assertion = read_json(contents[context['capture_assertion_path']], 'Antigravity assertion')
    if (assertion.get('schema_version') != ASSERTION_SCHEMA or assertion.get('run_id') != context['run_id']
            or assertion.get('repetition') != context['repetition']
            or assertion.get('native_inventory_sha256') != sha(contents['native/decode.json'])):
        raise ValueError('Antigravity assertion identity/inventory mismatch')
    for name in ('workload.json', 'observer.json'):
        if assertion.get('input_sha256', {}).get(name) != sha(contents['inputs/' + name]):
            raise ValueError('Antigravity workload/observer binding mismatch')
    docs = {}
    rows = assertion.get('capture_documents')
    if not isinstance(rows, list) or not rows:
        raise ValueError('Antigravity capture documents missing')
    for entry in rows:
        name = entry.get('path')
        if not isinstance(name, str) or PurePosixPath(name).is_absolute() or '..' in PurePosixPath(name).parts or name in docs:
            raise ValueError('Antigravity unsafe/duplicate capture path')
        data = contents.get('inputs/capture/' + name)
        if data is None or sha(data) != entry.get('sha256') or len(data) != entry.get('size_bytes'):
            raise ValueError('Antigravity captured member changed')
        docs[name] = data
    required = {'manifest.json', 'workload.json', 'capture-result.json', 'r1.stdout.jsonl', 'r2.stdout.jsonl',
                'r2-native-receipt.json', 'helper-ledger.jsonl', 'bench_check.py', 'workspace/fixture_project/checkout.py'}
    if not required <= docs.keys():
        raise ValueError('Antigravity captured proof incomplete')
    qualification = read_json(docs['manifest.json'], 'qualification manifest')
    if qualification.get('schema_version') != 'session-bench-antigravity-diagnostic-v1' or qualification.get('attempt_id') != context['run_id'] or qualification.get('repetition') != context['repetition']:
        raise ValueError('Antigravity qualification identity mismatch')
    indexed = qualification['files']
    if {entry['path'] for entry in indexed} != {name for name in docs if name != 'manifest.json' and not name.startswith('workspace/')}:
        raise ValueError('Antigravity qualification file set changed')
    for entry in indexed:
        data = docs[entry['path']]
        if sha(data) != entry['sha256'] or len(data) != entry['size_bytes']:
            raise ValueError('Antigravity qualification member changed')
    state = read_json(docs['capture-result.json'], 'capture result')
    receipt = read_json(docs['r2-native-receipt.json'], 'native receipt')
    if state.get('native_primary') != receipt.get('primary_path'):
        raise ValueError('Antigravity native boundary misrepresented')
    declared = {entry['path'] for entry in native_inventory['artifacts']}
    if declared != {'capture/' + entry['relative_path'] for entry in receipt['artifacts']}:
        raise ValueError('Antigravity native inventory differs from acquisition receipt')
    for entry in receipt['artifacts']:
        name = 'capture/' + entry['relative_path']
        data = contents.get('native/' + name)
        if data is None or sha(data) != entry['sha256'] or len(data) != entry['size_bytes'] or docs.get('native/' + entry['relative_path']) != data:
            raise ValueError('Antigravity native companion changed')
    if receipt.get('schema_version') == 'antigravity-session-root-v1':
        validate_bounded_root_receipt(receipt, docs, native_inventory,
                                      run_id=context['run_id'], primary_path=state['native_primary'])
    elif receipt.get('complete_family') is not False:
        raise ValueError('Antigravity native boundary misrepresented')
    if docs['bench_check.py'] != contents['runtime/fixtures/scenarios/survival-v1/workload/fixture_project/bench_check.py']:
        raise ValueError('Antigravity helper differs from frozen source')
    if sha(docs['workspace/fixture_project/checkout.py']) != state['after_sha256']:
        raise ValueError('Antigravity workspace changed')
    workspace = assertion.get('captured_workspace')
    if not isinstance(workspace, str) or not workspace.endswith('/' + context['run_id'] + '/workspace'):
        raise ValueError('Antigravity workspace declaration invalid')
    if canonical(observer_from_capture_documents(docs, workspace)) != contents['inputs/observer.json'] or docs['workload.json'] != contents['inputs/workload.json']:
        raise ValueError('Antigravity independent observer/workload differs')
    if context['complete_record_family'] or context['complete_root'] or context['root_repetitions'] not in (None, []):
        raise ValueError('Antigravity family/root proof unavailable')
    return False, False


def add_native_final_after_order(decoded, instance):
    """Project ``final_after`` only from a strict native step order.

    R2, the one edit call on the target, the one native edit completion that
    names the target, the one final helper call, the one result with the final
    helper line and exit code 0, and the final response with the R2 canary
    must have rising ``step_index`` values inside the R2 turn. No observer
    fact and no call-result link is used.
    """
    if decoded['status'] != 'ok' or len(decoded['turns']) != 2:
        return
    r2 = decoded['turns'][1]
    inside = lambda rows: [row for row in rows if row.get('turn_id') == r2['id']]
    command = f"python3 bench_check.py final --run-canary {instance['run_canary']}"
    chain = (
        [row for row in inside(decoded['actions']) if row['name'] == 'edit' and row.get('target') == TARGET],
        [row for row in inside(decoded['results']) if row.get('native_tool') in EDIT_TOOLS and row.get('target') == TARGET and row.get('status') == 'success'],
        [row for row in inside(decoded['actions']) if row.get('command') == command],
        [row for row in inside(decoded['results']) if row.get('exit_code') == 0 and re.match(r'SB_SURVIVAL_V1_HELPER_FINAL_', row.get('output', ''))],
        [row for row in inside(decoded['responses']) if row['phase'] == 'final_answer' and row.get('canary') == instance['turns'][1]['response_canary']],
    )
    if any(len(rows) != 1 for rows in chain):
        return
    steps = [r2['sequence'], *(rows[0]['sequence'] for rows in chain)]
    if any(later <= earlier for earlier, later in zip(steps, steps[1:])):
        return
    final = chain[2][0]
    decoded['relations'].append({'kind': 'final_after', 'from_id': r2['id'], 'to_id': final['id'], 'locator': final['locator'],
                                 'native_order_steps': steps, 'method': 'strict native step_index order inside the R2 turn'})


def scored_action_pool(decoded, observer):
    """Native calls that stand for the scored actions.

    The stream and the transcript share no call id, so the comparator cannot
    drop a native call by identity. A native call leaves the pool only when it
    matches no scored observer action and exactly one unscored one, which
    matches no other native call (a discovery read). A repeated call that the
    native record cannot tell from a scored call stays as a duplicate.
    """
    from .live_metric_comparator import _match_action, _match_many, _match_turn, _native_id, _observer_events
    events = observer['events']
    _, _, matched, _ = _match_many(_observer_events(events, 'user_turn'), decoded['turns'], _match_turn)
    turn_map = {_native_id(candidate): expected for expected, candidate in matched.items()}
    same = lambda event, action: _match_action(event, action, turn_map).value is True
    scored = _observer_events(events, 'action')
    unscored = [event for event in events if event['kind'] == 'action' and event['population_role'] == 'unscored']
    kept = []
    for action in decoded['actions']:
        if not any(same(event, action) for event in scored):
            twins = [event for event in unscored if same(event, action)]
            if len(twins) == 1 and sum(same(twins[0], other) for other in decoded['actions']) == 1:
                continue
        kept.append(action)
    return kept


def apply_antigravity_native_absence(measurement, decoded, *, complete_root):
    """Resolve model identity and response usage as native_absent when the complete root proves it.

    The comparator leaves these rows unresolved when a native response exists
    but holds no model, or holds token counts that are not a complete usage
    record (input, output, cache-read and cache-write). With a complete,
    cleanly decoded root that is a proven absence of the scored record.
    Without a complete root nothing changes.
    """
    rows = [dict(row) for row in measurement['metrics']]
    if complete_root is True and decoded.get('status') == 'ok' and not decoded.get('diagnostics'):
        responses = [row for row in decoded.get('responses', []) if row.get('phase') == 'final_answer']
        model = [row for row in responses if row.get('model_id') is not None or row.get('model') is not None]
        # Usage and token semantics follow the shared comparator rule; there is no override here.
        absent = {'attribution.model_config': not model}
        for row in rows:
            if absent.get(row['id']) and row['state'] == 'unresolved' and row['correct'] == 0:
                row['state'] = 'native_absent'
    return {**measurement, 'metrics': rows}


def build_session_root_replay_evidence(root, native, instance, context, common):
    """Capture kind v2: decode the copied directory and build real broad evidence."""
    from .antigravity_format_evidence import build_antigravity_format_evidence
    state = read_json((root / 'inputs/capture/capture-result.json').read_bytes(), 'capture result')
    primary = state['native_primary']
    decoded = decode_antigravity_family(Path(native) / 'capture', primary)
    add_native_final_after_order(decoded, instance)
    observer = read_json((root / 'inputs/observer.json').read_bytes(), 'observer')
    projected = {**decoded, 'actions': scored_action_pool(decoded, observer)}
    common = dict(common)
    name = 'inputs/capture/r2-native-receipt.json'
    common['antigravity_root_locators'] = [{'id': name, 'sha256': sha((root / name).read_bytes())}]
    return decoded, projected, build_antigravity_format_evidence(decoded, context=context, common=common, primary=primary)


def build_antigravity_replay_evidence(root, native, instance, context, common):
    if context.get('observer_kind') == 'antigravity-capture-v3':
        from .antigravity_state_inputs import build_state_root_replay_evidence
        return build_state_root_replay_evidence(root, native, instance, context, common)
    if context.get('observer_kind') == OBSERVER_KIND_V2:
        return build_session_root_replay_evidence(root, native, instance, context, common)
    state = read_json((root / 'inputs/capture/capture-result.json').read_bytes(), 'capture result')
    selected = native / 'capture' / state['native_primary']
    decoded = decode_antigravity_native(selected)
    projected = decoded
    receipt = read_json((root / 'inputs/capture/r2-native-receipt.json').read_bytes(), 'native receipt')
    # Only new closed-session captures opt into the source-bound diagnostic.
    # The historical transcript-only packets keep their exact replay output.
    if receipt.get('schema_version') == 'antigravity-session-root-v1':
        observer = read_json((root / 'inputs/observer.json').read_bytes(), 'observer')
        responses = {event['id']: event['fields']['text'] for event in observer['events']
                     if event['kind'] == 'assistant_response'}
        if set(responses) != {'response-r1', 'response-r2'}:
            raise ValueError('Antigravity displayed response population differs')
        session_id = state['native_primary'].split('/', 1)[0]
        diagnostic = []
        for turn in (1, 2):
            raw = (root / f'inputs/capture/r{turn}.stdout.jsonl').read_bytes()
            projection = _bound_final_response_usage(raw, expected_response=responses[f'response-r{turn}'],
                                                     expected_session_id=session_id)
            projection['response_id'] = f'response-r{turn}'
            diagnostic.append(projection)
        projected = dict(decoded)
        projected['response_usage_diagnostic'] = diagnostic
        projected['bounded_root_diagnostic'] = {
            'receipt_schema': receipt['schema_version'],
            'scope': receipt['scope'],
            'external_companions_established': False,
            'stable_root_scored': False,
        }
    # All raw native records are retained; unsupported rows block only the
    # semantics they do not establish, never an invented action-result join.
    # The retained boundary explicitly disclaims a complete native family.
    # Broad profile evidence requires that boundary; no synthetic control rows.
    broad = {
        'broad.readable_rationale': {'evidence_complete': False, 'response_ids': [], 'records': []},
        'broad.thread_structure': {'evidence_complete': False, 'session_id': None, 'turns': [], 'explicit_parentage': False},
        'broad.standard_tools_readable': {'evidence_complete': False, 'container': 'jsonl', 'parser': 'Python JSON',
            'vendor_binary_required': False, 'account_required': False, 'backend_required': False, 'network_required': False},
        'broad.documented_format': {'evidence_complete': False, 'document_id': None, 'mapping': {}},
        'broad.self_contained_identity': {'evidence_complete': False, 'session_id': None, 'harness': 'antigravity',
            'surface': 'cli', 'record_family': 'selected step JSONL', 'external_lookup_required': None, 'absolute_path_required': None},
        'broad.declared_format_version': {'evidence_complete': False, 'format_version': None, 'machine_readable': False, 'bundle_binding': None},
        'broad.event_timestamps': {'evidence_complete': False, 'event_ids': [], 'records': []},
        'broad.honest_version_signal': {'evidence_complete': False, 'declared_version': None, 'decoder_contract_version': '1',
            'incompatible_schema_distinguished': True, 'matches_decoder_contract': decoded['status'] == 'ok'},
        'broad.observed_schema_stability': {'evidence_complete': False, 'advertised_contract': None, 'observations': [], 'exceptions': decoded['diagnostics']},
        'broad.stable_root_location': {'evidence_complete': False, 'repetitions': []},
        'broad.naive_reader_duplicate_safety': {'evidence_complete': False, 'event_ids': [], 'forward_records': [],
            'deduplication': {'documented': False, 'rule': ''}},
        'broad.classified_content_density': {'evidence_complete': False, 'classification_rule': CLASSIFIED_CONTENT_DENSITY_RULE, 'records': []},
    }
    profile = {'schema_version': 'session-bench-format-profile-v1', 'run_id': context['run_id'],
               'configuration_id': 'antigravity', 'repetition': context['repetition'], 'broad_evidence': broad}
    return decoded, projected, {'profile': profile}
