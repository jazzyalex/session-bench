"""Replay inputs for a whole-state Antigravity capture (receipt ``antigravity-state-root-v1``).

The conversation database ``conversations/<conversation-id>.db`` is the
primary record. The family is every file whose path carries the conversation
id. It is complete: the receipt binds each file, and the two metadata
inventories of the whole state directory account for every changed entry.
The root is not complete: shared stores changed during the run and their
content cannot be copied whole (it holds other conversations). So
``portable.complete_root`` is false. The rows of this conversation in those
stores were read after the capture on the owner's decision and are bound in
the packet (``shared-store-extract.json``). They hold only known summary
fields, so an absence in the database is not contradicted by them.
"""
from pathlib import Path

from .antigravity_score_inputs import (
    add_native_final_after_order, observer_from_capture_documents, read_json, sha,
)
from .native_replay import canonical

ASSERTION_SCHEMA_V3 = 'session-bench-antigravity-score-inputs-v3'
OBSERVER_KIND_V3 = 'antigravity-capture-v3'
STATE_ROOT_SUFFIX = '/.gemini/antigravity-cli'
STATE_ROOT_LOCATOR = ('~/.gemini/antigravity-cli/conversations/<conversation-id>.db with brain/<conversation-id>/, '
                      'annotations/<conversation-id>.pbtxt and presence/<conversation-id>.lock (the one new conversation between '
                      'two metadata-only inventories of the whole state directory)')
CAPTURE_DOCUMENTS_V3 = ('workload.json', 'capture-start.json', 'capture-result.json', 'r1.stdout.jsonl', 'r2.stdout.jsonl',
                        'r1.stderr.txt', 'r2.stderr.txt', 'r2-native-receipt.json', 'state-before.json', 'r2-state-after.json',
                        'helper-ledger.jsonl', 'bench_check.py', 'workspace/fixture_project/checkout.py')
# The rows of this conversation in the shared stores, read after the capture on the owner's decision.
EXTRACT = 'shared-store-extract.json'
EXTRACT_SCHEMA = 'session-bench-antigravity-shared-store-extract-v1'
SUMMARY_COLUMNS = frozenset({
    'conversation_id', 'title', 'preview', 'step_count', 'last_modified_time', 'workspace_uris', 'status', 'source', 'project_id',
    'agent_name', 'parent_conversation_id', 'nesting_depth', 'battle_id', 'winning_conversation_id', 'not_fully_idle', 'killed',
    'last_user_input_time', 'last_user_input_step_index', 'app_data_dir', 'raw_summary', 'group_id'})
# Field map of the summary message (``raw_summary``; field 2 of a jetbox entry). Paths in SUMMARY_MESSAGES are
# nested messages. Every other path is a leaf: {path: (wire kind, meaning)}. None of them is a token count.
SUMMARY_MESSAGES = frozenset({'3', '7', '9', '9.3', '10', '15', '17', '17.1', '17.1.3', '17.2'})
SUMMARY_FIELDS = {
    '1': ('bytes', 'title'), '2': ('varint', 'step count'), '3.1': ('varint', 'last modified, seconds'), '3.2': ('varint', 'last modified, nanoseconds'),
    '4': ('bytes', 'trajectory id'), '5': ('varint', 'status enum'), '7.1': ('varint', 'created, seconds'), '7.2': ('varint', 'created, nanoseconds'),
    '9.1': ('bytes', 'workspace URI'), '9.2': ('bytes', 'repository root URI'), '9.3.1': ('bytes', 'repository name'),
    '9.3.2': ('bytes', 'repository URL'), '9.4': ('bytes', 'branch'), '10.1': ('varint', 'last user input, seconds'),
    '10.2': ('varint', 'last user input, nanoseconds'), '15.1': ('bytes', 'title'), '16': ('varint', 'step index of the last user input'),
    '17.1.1': ('bytes', 'workspace URI'), '17.1.2': ('bytes', 'repository root URI'), '17.1.3.1': ('bytes', 'repository name'),
    '17.1.3.2': ('bytes', 'repository URL'), '17.1.4': ('bytes', 'branch'), '17.2.1': ('varint', 'created, seconds'),
    '17.2.2': ('varint', 'created, nanoseconds'), '17.3': ('bytes', 'workspace id'), '17.6': ('bytes', 'conversation id'),
    '17.7': ('bytes', 'workspace URI'), '17.18': ('bytes', 'project id'), '22': ('varint', 'trajectory type enum')}


def state_root_row(repetition):
    return {'repetition': repetition, 'root_locator': STATE_ROOT_LOCATOR, 'discovery_mode': 'metadata_safe_normal_root',
            'personal_history_scanned': False}


def validate_state_root_capture(contents, context, native_inventory):
    """Check the whole-state receipt against its two inventories and the copied family, or raise.

    Returns (complete record family, complete root) = (True, False).
    """
    from .antigravity_root_evidence import (
        RootClosureError, _sqlite_groups, _state_entries, _state_statement, classify_state_changes, inventory_sha256,
    )
    assertion = read_json(contents[context['capture_assertion_path']], 'Antigravity assertion')
    if (assertion.get('schema_version') != ASSERTION_SCHEMA_V3 or assertion.get('run_id') != context['run_id']
            or assertion.get('repetition') != context['repetition']
            or assertion.get('native_inventory_sha256') != sha(contents['native/decode.json'])):
        raise ValueError('Antigravity assertion identity/inventory mismatch')
    for name in ('workload.json', 'observer.json'):
        if assertion.get('input_sha256', {}).get(name) != sha(contents['inputs/' + name]):
            raise ValueError('Antigravity workload/observer binding mismatch')
    rows = assertion.get('capture_documents')
    listed = [entry.get('path') if isinstance(entry, dict) else None for entry in rows] if isinstance(rows, list) else None
    if listed not in (sorted(CAPTURE_DOCUMENTS_V3), sorted((*CAPTURE_DOCUMENTS_V3, EXTRACT))):
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
    receipt = read_json(docs['r2-native-receipt.json'], 'native receipt')
    before, after = read_json(docs['state-before.json'], 'state before'), read_json(docs['r2-state-after.json'], 'state after')
    turns = state.get('turns')
    if (state.get('status') != 'captured_pending_qualification' or state.get('attempt_id') != context['run_id']
            or state.get('configuration_id') != 'antigravity' or state.get('version') != context['build']
            or state.get('state_root_scope') != 'whole_cli_state_directory'
            or not isinstance(turns, list) or [row.get('turn') if isinstance(row, dict) else None for row in turns] != [1, 2]):
        raise ValueError('Antigravity capture completion mismatch')
    for row in turns:
        if (row.get('exit_code') != 0 or row.get('response_canary_observed') is not True
                or row.get('stdout_sha256') != sha(docs[f"r{row['turn']}.stdout.jsonl"])):
            raise ValueError('Antigravity turn receipt differs from the retained stdout')
    session = state.get('conversation_id')
    quiet = receipt.get('quiescence') if isinstance(receipt.get('quiescence'), dict) else {}
    digests = quiet.get('inventory_sha256') if isinstance(quiet.get('inventory_sha256'), list) else []
    if (receipt.get('schema_version') != 'antigravity-state-root-v1' or receipt.get('scope') != 'whole_cli_state_directory'
            or receipt.get('attempt_id') != context['run_id'] or receipt.get('turn') != 2 or receipt.get('conversation_id') != session
            or receipt.get('primary_path') != state.get('native_primary') or receipt.get('started_ns') != state.get('started_ns')
            or receipt.get('preexisting_contents_opened') is not False or receipt.get('metadata_only_before_after') is not True
            or not isinstance(receipt.get('source_root'), str) or not receipt['source_root'].endswith(STATE_ROOT_SUFFIX)
            or before.get('root') != receipt['source_root'] or receipt.get('requested_model') != state.get('requested_model')):
        raise ValueError('Antigravity whole-state receipt identity differs')
    if (inventory_sha256(before) != receipt.get('before_inventory_sha256') or inventory_sha256(after) != receipt.get('after_inventory_sha256')
            or quiet.get('stable') is not True or quiet.get('unchanged_after_copy') is not True or quiet.get('reads') != len(digests)
            or len(digests) < 2 or digests[-1] != digests[-2] or digests[-1] != receipt['after_inventory_sha256']):
        raise ValueError('Antigravity whole-state inventory or quiescence differs')
    try:
        classes = classify_state_changes(before, after, started_ns=receipt['started_ns'], conversation_id=session)
    except RootClosureError as error:
        raise ValueError(f'Antigravity whole-state classification failed: {error}') from error
    accounting = receipt.get('accounting') if isinstance(receipt.get('accounting'), dict) else {}
    shared = sorted(row['relative_path'] for row in classes['shared_changed'])
    if (any(receipt.get('classes', {}).get(name) != classes[name] for name in ('session_owned', 'run_owned', 'shared_changed', 'directories_changed'))
            or receipt.get('session_directories') != classes['session_directories']
            or accounting.get('changed_entries') != classes['changed_entries'] or accounting.get('unexplained') != []
            or accounting.get('every_changed_entry_accounted') is not True
            or accounting.get('shared_stores_changed_content_not_read') != shared
            or accounting.get('statement') != _state_statement(classes)
            or receipt.get('sqlite_groups') != _sqlite_groups(classes['session_owned'])):
        raise ValueError('Antigravity whole-state classification differs from the receipt')
    entries = _state_entries(after)
    artifacts = {row.get('relative_path'): row for row in receipt.get('artifacts', []) if isinstance(row, dict)}
    if set(artifacts) != set(classes['session_owned']) | set(classes['run_owned']):
        raise ValueError('Antigravity copied file set differs from the classification')
    declared = {entry['path'] for entry in native_inventory['artifacts']}
    if declared != {'capture/' + name for name in classes['session_owned']} or {'native/' + name for name in declared} != {
            name for name in contents if name.startswith('native/capture/')}:
        raise ValueError('Antigravity native inventory differs from the session-owned files')
    for name in classes['session_owned']:
        row, entry, data = artifacts[name], entries[name], contents['native/capture/' + name]
        if (row.get('class') != 'session_owned' or row.get('private_only') is not False
                or row.get('filesystem_id') != f"{entry['device']}:{entry['inode']}" or row.get('size_bytes') != entry['size_bytes']
                or len(data) != entry['size_bytes'] or row.get('sha256') != sha(data)):
            raise ValueError('Antigravity session-owned member differs')
    for name in classes['run_owned']:
        if artifacts[name].get('class') != 'run_owned' or artifacts[name].get('private_only') is not True:
            raise ValueError('Antigravity run-owned member is not marked private')
    primary = f'capture/conversations/{session}.db'
    if primary not in declared:
        raise ValueError('Antigravity conversation database is missing')
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
    if context.get('required_companions') != sorted(name for name in declared if name != primary):
        raise ValueError('Antigravity companion set differs from the copied family')
    if context.get('root_repetitions') != [state_root_row(context['repetition'])]:
        raise ValueError('Antigravity root row differs from the whole-state receipt')
    if EXTRACT in docs:
        from .antigravity_conversation_db import read_conversation_db
        findings = shared_store_findings(docs[EXTRACT], read_conversation_db(contents['native/' + primary]), session)
        if not findings['proved']:
            raise ValueError('Antigravity shared-store extract proves nothing: ' + '; '.join(findings['reasons']))
    # Shared stores changed and cannot be copied whole: the family is complete, the root is not.
    if shared and context.get('complete_root') is not False:
        raise ValueError('Antigravity root with shared stores outside the copy cannot be declared complete')
    return True, not shared


def _summary_leaves(data, prefix=''):
    """(path, wire kind, value) of every leaf of a summary message, by the field map. Raises ValueError when it is not one."""
    from .antigravity_conversation_db import parse_message
    leaves = []
    for number, wire, value, _, _ in parse_message(data):
        path = f'{prefix}{number}'
        if path in SUMMARY_MESSAGES and wire == 2:
            leaves += _summary_leaves(value, path + '.')
        else:
            leaves.append((path, {0: 'varint', 2: 'bytes'}.get(wire, 'fixed'), value))
    return leaves


def shared_store_findings(extract, store, session_id):
    """What the shared-store rows of the conversation hold, and whether they prove that no token count is there.

    ``proved`` is true only when: the extract names this conversation; the
    summary row has exactly the known columns; the row's protobuf summary and
    the one jetbox entry hold only fields of the documented map with the
    documented wire kind; and the summary describes the captured database
    (conversation id, trajectory id, step count, last user step). An unknown
    column or field could be a count, so it proves nothing.
    """
    from .antigravity_conversation_db import STEP_USER
    reasons, fields = [], []
    if extract is None:
        return {'proved': False, 'reasons': ['shared-store extract is missing'], 'fields': []}
    try:
        document = read_json(extract, 'shared-store extract')
        row = document['conversation_summaries_db']['row']
        entries = document['jetbox_summaries_proto_pb']['entries_with_this_id']
        if document.get('schema_version') != EXTRACT_SCHEMA or document['conversation_summaries_db'].get('table') != 'conversation_summaries':
            raise ValueError('schema')
        if document.get('conversation_id') != session_id or row.get('conversation_id') != session_id:
            reasons.append('extract names another conversation')
        reasons += [f'unknown column {name}' for name in sorted(set(row) - SUMMARY_COLUMNS)]
        if len(entries) != 1:
            reasons.append('jetbox entry count is not one')
        blobs = [('raw_summary', bytes.fromhex(row['raw_summary']['hex']), '')]
        blobs += [('jetbox_entry', bytes.fromhex(entry), '2.') for entry in entries]
        summaries = []
        for label, data, prefix in blobs:
            from .antigravity_conversation_db import field, one
            body = data if not prefix else one(data, 2)
            if prefix and (one(data, 1) != session_id.encode() or body is None or len(field(data, 1)) + len(field(data, 2)) != len(_summary_leaves(data, 'x'))):
                reasons.append('jetbox entry is not one id and one summary')
                continue
            leaves = _summary_leaves(body)
            for path, kind, _ in leaves:
                fields.append([label, prefix + path, kind])
                if SUMMARY_FIELDS.get(path, (None,))[0] != kind:
                    reasons.append(f'unknown field {label}:{prefix}{path}')
            if prefix:
                fields.insert(len(fields) - len(leaves), [label, '1', 'bytes'])
            summaries.append({path: value for path, _, value in leaves})
    except (KeyError, TypeError, ValueError, AttributeError):
        return {'proved': False, 'reasons': ['shared-store extract is malformed'], 'fields': []}
    steps = store['tables'].get('steps', [])
    meta = store['tables'].get('trajectory_meta', [])
    users = [step['idx'] for step in steps if step.get('step_type') == STEP_USER]
    expected = {'2': len(steps), '16': users[-1] if users else None, '4': meta[0]['trajectory_id'].encode() if len(meta) == 1 else None,
                '17.6': session_id.encode()}
    if not reasons and (any(summary.get(path) != value for summary in summaries for path, value in expected.items())
                        or row.get('step_count') != len(steps) or row.get('last_user_input_step_index') != expected['16']):
        reasons.append('summary does not describe the captured conversation')
    return {'proved': not reasons, 'reasons': reasons, 'fields': fields}


def apply_shared_store_absence(measurement, decoded, findings):
    """Resolve token semantics and reconciliation as native_absent from evidence in hand.

    The family is complete, so the database is known whole. The shared-store
    rows of the conversation are bound in the packet and hold only known
    summary fields. Then a usage record without a cache-write key, and a
    conversation without any turn or session total, are proven absences. The
    usage record stays in the decoded population. Nothing changes when the
    decode is not clean or the rows prove nothing.
    """
    rows = [dict(row) for row in measurement['metrics']]
    if findings.get('proved') is True and decoded.get('status') == 'ok' and not decoded.get('diagnostics'):
        usage = next((row for row in rows if row['id'] == 'attribution.usage'), {})
        for row in rows:
            if row['state'] != 'unresolved' or row['correct'] != 0:
                continue
            if row['id'] == 'attribution.token_semantics' and row['decoded_eligible'] > 0 and usage.get('correct', 0) > 0:
                row['state'] = 'native_absent'
            elif row['id'] == 'attribution.reconciliation' and row['decoded_eligible'] == 0:
                row['state'] = 'native_absent'
    return {**measurement, 'metrics': rows}


def packet_shared_store_absence(measurement, decoded, root, native):
    """Replay entry point: read the bound extract and the database of this packet."""
    from .antigravity_conversation_db import ConversationDatabaseError, read_conversation_db
    from .antigravity_live import conversation_db_path
    path = Path(root) / 'inputs/capture' / EXTRACT
    session = decoded.get('session_id')
    try:
        store = read_conversation_db((Path(native) / 'capture' / conversation_db_path(session)).read_bytes())
    except (ConversationDatabaseError, OSError):
        return measurement
    return apply_shared_store_absence(measurement, decoded, shared_store_findings(path.read_bytes() if path.is_file() else None, store, session))


def paired_action_pool(decoded, observer):
    """Native calls that stand for the scored actions (rule for unscored native actions).

    The stream and the native record share no call id. In one turn, for one
    group of native calls with equal name, arguments and target, the observer
    saw n scored and m unscored actions and the native record holds k. Then
    min(k, n + m) are paired. The pool keeps the calls paired with a scored
    action and the k - (n + m) calls above the observed count, which are
    duplicates. A call paired with an unscored action leaves the pool.
    """
    from .live_metric_comparator import _match_action, _match_many, _match_turn, _native_id, _observer_events
    events = observer['events']
    _, _, matched, _ = _match_many(_observer_events(events, 'user_turn'), decoded['turns'], _match_turn)
    turn_map = {_native_id(candidate): expected for expected, candidate in matched.items()}
    same = lambda event, action: _match_action(event, action, turn_map).value is True
    scored = _observer_events(events, 'action')
    unscored = [event for event in events if event['kind'] == 'action' and event['population_role'] == 'unscored']
    groups = {}
    for action in decoded['actions']:
        key = canonical([action.get('turn_id'), action.get('name'), action.get('input'), action.get('target')])
        groups.setdefault(key, []).append(action)
    kept = set()
    for members in groups.values():
        n = sum(same(event, members[0]) for event in scored)
        m = sum(same(event, members[0]) for event in unscored)
        # A group that the observer did not see at all stays whole: every call is a dangling candidate.
        keep = len(members) if n + m == 0 else min(len(members), n) + max(0, len(members) - (n + m))
        kept.update(id(action) for action in members[:keep])
    return [action for action in decoded['actions'] if id(action) in kept]


def build_state_root_replay_evidence(root, native, instance, context, common):
    """Decode the conversation database and build broad evidence for the whole-state family."""
    from .antigravity_format_evidence import build_state_format_evidence
    from .antigravity_live import decode_antigravity_conversation
    state = read_json((root / 'inputs/capture/capture-result.json').read_bytes(), 'capture result')
    session = state['conversation_id']
    decoded = decode_antigravity_conversation(Path(native) / 'capture', session)
    add_native_final_after_order(decoded, instance)
    observer = read_json((root / 'inputs/observer.json').read_bytes(), 'observer')
    projected = {**decoded, 'actions': paired_action_pool(decoded, observer)}
    common = dict(common)
    names = ['inputs/capture/r2-native-receipt.json', 'inputs/capture/state-before.json', 'inputs/capture/r2-state-after.json']
    common['antigravity_root_locators'] = [{'id': name, 'sha256': sha((root / name).read_bytes())} for name in names]
    return decoded, projected, build_state_format_evidence(decoded, projected, context=context, common=common, session_id=session)
