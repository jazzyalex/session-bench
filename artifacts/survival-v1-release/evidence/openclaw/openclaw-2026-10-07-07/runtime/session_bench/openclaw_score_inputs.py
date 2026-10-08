"""Replay inputs for an OpenClaw capture on the route ``acp`` (receipt ``openclaw-state-root-v1``).

OpenClaw runs in the owner's normal state. A session has no file of its own
in the OpenClaw stores: its record is its rows in the shared agent store
``openclaw-agent.sqlite``. The controller lists the whole OpenClaw home by
metadata before the gateway starts and after it stopped, accounts for every
changed entry, and exports the rows of the test session from private copies
of four shared stores.

The family is what the capture bound for the session: every exported row, and
the three files named by the Codex thread id (rollout, shell snapshot, lock).
The read is three tables of the agent store (``openclaw_session_rows``). The
packet holds the row export and the schema export. The files of the Codex
thread are not in the packet: the decoder never opens them, and density takes
their sizes from the receipt.

The root is not complete: the stores hold other sessions and cannot be copied
whole, so ``portable.complete_root`` is false. The family is complete in a
limited sense, stated in the adapter document: every row with an exact id
match in a table that the deny rule allows, and every file named by an id.

The observer is independent of the stores: the submitted prompts, the ACP
stream of each turn (tool calls with arguments, status and exit code; the
final text), the helper ledger that the frozen helper writes itself, the
workspace copies, and the start line of the gateway (the model).
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from .live_metric_comparator import _validate_observer
from .native_replay import canonical
from .openclaw_acp_observer import OpenClawAcpError, build_openclaw_acp_observer, turn_summary
from .openclaw_format_evidence import build_openclaw_format_evidence
from .openclaw_session_rows import (
    AGENT_STORE, ROWS_FILE, ROWS_SCHEMA, SCHEMA_FILE, add_native_final_after_chain, decode_openclaw_native, read_rows, remove_final_response,
    store_schema_sha256,
)

ASSERTION_SCHEMA = 'session-bench-openclaw-score-inputs-v1'
OBSERVER_KIND = 'openclaw-acp-capture-v1'
CAPTURE_SCHEMA = 'session-bench-openclaw-survival-capture-v2'
RECEIPT, BEFORE, AFTER = 'r2-native-receipt.json', 'state-before.json', 'r2-state-after.json'
_TURN_DOCUMENTS = ('launch.json', 'acp-stream.jsonl', 'acp-receipt.json', 'workspace/fixture_project/.survival-observer.jsonl',
                   'workspace/fixture_project/checkout.py', 'workspace/fixture_project/bench_check.py')
CAPTURE_DOCUMENTS = ('plan.json', 'capture-result.json', 'preflight.json', 'workload-instance.json', 'workload-template.json',
                     'observer/prompt-r1.txt', 'observer/prompt-r2.txt', 'workspaces/before/fixture_project/checkout.py',
                     'gateway-launch.json', 'gateway-stop.json', 'acp-launch.json', 'fixture-placement.json', 'fixture-removal.json',
                     BEFORE, AFTER, RECEIPT) + tuple(f'turn-r{turn}/{name}' for turn in (1, 2) for name in _TURN_DOCUMENTS)
REQUIRED_COMPANIONS = ['capture/' + SCHEMA_FILE]
STATE_ROOT_SUFFIX = '/.openclaw'
ROOT_LOCATOR = ('~/.openclaw/agents/main/agent/openclaw-agent.sqlite: the rows of the session id that the table session_nodes gives for the '
                'session key of the run (the controller makes the key and passes it to `openclaw acp --session`); the tables '
                'transcript_events, trajectory_runtime_events and session_windows hold that id in session_id; '
                'found by two metadata-only inventories of the whole OpenClaw home and a row lookup by the key')
_PHASES = ('inspect', 'baseline', 'final')


def sha(data):
    return hashlib.sha256(data).hexdigest()


def read_json(data, label):
    try:
        return json.loads(data)
    except (ValueError, TypeError) as error:
        raise ValueError(f'{label} is not JSON') from error


def _check(condition, message):
    if not condition:
        raise ValueError(message)


def state_root_row(repetition):
    return {'repetition': repetition, 'root_locator': ROOT_LOCATOR, 'discovery_mode': 'metadata_safe_normal_root',
            'personal_history_scanned': False}


def _independent_capture(documents):
    """Check the independent receipts of one ACP capture. No native row is opened.

    Returns (plan, workload, streams by turn, helper ledgers by turn, checkout before, checkout after, gateway model).
    """
    plan, result, workload = (read_json(documents[name], name) for name in ('plan.json', 'capture-result.json', 'workload-instance.json'))
    stop, launch, bridge = (read_json(documents[name], name) for name in ('gateway-stop.json', 'gateway-launch.json', 'acp-launch.json'))
    run_id, key = plan.get('attempt_id'), plan.get('session_key')
    _check(plan.get('schema_version') == result.get('schema_version') == CAPTURE_SCHEMA and plan.get('configuration_id') == 'openclaw'
           and plan.get('route') == result.get('route') == 'acp' and isinstance(run_id, str) and run_id
           and result.get('attempt_id') == workload.get('run_id') == run_id and isinstance(key, str) and key == result.get('session_key')
           and isinstance(plan.get('key_id'), str) and key.endswith(':' + plan['key_id'])
           and result.get('status') == 'captured_pending_qualification' and result.get('model_submissions') == 2
           and result.get('state_root_scope') == 'whole_openclaw_home' and result.get('gateway_stopped') is True
           and result.get('fixture_removed') is True and plan.get('no_model_override') is True
           and type(plan.get('repetition')) is int and plan['repetition'] in (1, 2, 3), 'OpenClaw capture identity or completion mismatch')
    _check(workload.get('run_canary') == 'SB_SURVIVAL_V1_RUN_' + run_id, 'OpenClaw workload canary mismatch')
    _check(sha(documents['workload-template.json']) == plan.get('workload_sha256'), 'OpenClaw workload template digest mismatch')
    # The gateway ran in the foreground with channels and timed jobs switched off, and it stopped.
    environment = launch.get('environment_overrides') if isinstance(launch.get('environment_overrides'), dict) else {}
    _check(launch.get('argv') == plan.get('gateway_argv') and launch.get('foreground') is True and launch.get('service_installed') is False
           and environment == plan.get('gateway_environment') and environment.get('OPENCLAW_SKIP_CHANNELS') == '1'
           and environment.get('OPENCLAW_SKIP_CRON') == '1' and stop.get('stopped') is True and stop.get('port_closed') is True
           and bridge.get('argv') == plan.get('acp_argv') and bridge.get('session_key') == key and bridge.get('model_override') is None,
           'OpenClaw gateway or ACP launch differs from the plan')
    model = stop.get('agent_model')
    _check(isinstance(model, dict) and isinstance(model.get('provider'), str) and isinstance(model.get('model'), str)
           and (model['provider'], model['model']) == (result.get('provider'), result.get('model')), 'OpenClaw gateway model line mismatch')
    turns, receipts, protected = workload.get('turns'), result.get('turns'), plan.get('protected_sha256')
    _check(isinstance(turns, list) and len(turns) == 2 and isinstance(receipts, list) and len(receipts) == 2
           and isinstance(protected, dict) and {'bench_check.py', 'checkout.py'} <= set(protected), 'OpenClaw requires two captured turns')
    before = documents['workspaces/before/fixture_project/checkout.py']
    _check(sha(before) == protected['checkout.py'], 'OpenClaw initial checkout digest mismatch')
    streams, ledgers, sessions = {}, {}, set()
    for number, (turn, receipt) in enumerate(zip(turns, receipts), 1):
        prefix = f'turn-r{number}/'
        sent, stream_receipt = (read_json(documents[prefix + name], name) for name in ('launch.json', 'acp-receipt.json'))
        prompt, stream = documents[f'observer/prompt-r{number}.txt'], documents[prefix + 'acp-stream.jsonl']
        _check(turn.get('id') == f'turn-r{number}' and turn.get('revision') == f'r{number}' and isinstance(turn.get('text'), str)
               and prompt == turn['text'].encode(), 'OpenClaw submitted prompt mismatch')
        _check(sent.get('method') == 'session/prompt' and sent.get('prompt_sha256') == sha(prompt) and sent.get('session_key') == key
               and sent.get('model_override') is None and sent.get('acp_session_id') == result.get('acp_session_id'),
               'OpenClaw prompt launch binding mismatch')
        _check(receipt.get('turn') == number and receipt.get('status') == 'completed' and receipt.get('stream_sha256') == sha(stream)
               and stream_receipt.get('stream_sha256') == sha(stream) and stream_receipt.get('stop_reason') == 'end_turn', 'OpenClaw stream digest mismatch')
        try:
            summary = turn_summary(stream, f'ACP stream R{number}')
        except OpenClawAcpError as error:
            raise ValueError(f'OpenClaw ACP stream is not a complete turn: {error}') from error
        canary = turn.get('response_canary')
        _check(summary['prompt'] == turn['text'] and summary['acp_session_id'] == result.get('acp_session_id')
               and all(seen == key for seen in summary['session_keys']) and isinstance(canary, str)
               and summary['text'].rstrip().endswith(canary) and summary['text'].count(canary) == 1, 'OpenClaw stream identity or canary mismatch')
        sessions.add(summary['acp_session_id'])
        _check(sha(documents[prefix + 'workspace/fixture_project/bench_check.py']) == protected['bench_check.py'] == workload.get('helper', {}).get('sha256'),
               'OpenClaw helper changed')
        ledger = documents[prefix + 'workspace/fixture_project/.survival-observer.jsonl']
        rows = [read_json(line, 'helper ledger') for line in ledger.splitlines() if line.strip()]
        _check(len(rows) == (2 if number == 1 else 3), 'OpenClaw missing or extra helper execution')
        for row, phase in zip(rows, _PHASES):
            nonce = workload['helper']['nonces'].get(phase)
            marker = f'SB_SURVIVAL_V1_HELPER_{phase.upper()}_{nonce} '
            _check(isinstance(nonce, str) and nonce and isinstance(row, dict) and row.get('schema_version') == '1.0-survival-helper-ledger'
                   and row.get('phase') == phase and row.get('helper_nonce') == nonce and row.get('id') == f'helper-{phase}-{nonce}'
                   and row.get('run_canary') == workload['run_canary'] and row.get('argv') == ['python3', 'bench_check.py', phase]
                   and row.get('cwd') == 'fixture_project' and type(row.get('exit_code')) is int
                   and row['exit_code'] == (1 if phase == 'baseline' else 0) and isinstance(row.get('output'), str)
                   and row['output'].startswith(marker), 'OpenClaw helper identity or outcome mismatch')
        streams[number], ledgers[number] = stream, ledger
    _check(len(sessions) == 1, 'OpenClaw turns used different ACP sessions')
    middle, after = (documents[f'turn-r{number}/workspace/fixture_project/checkout.py'] for number in (1, 2))
    _check(middle == before and middle != after, 'OpenClaw checkout revision boundary mismatch')
    for line in ledgers[2].splitlines():
        row = read_json(line, 'helper ledger')
        _check(row.get('checkout_sha256') == sha(after if row['phase'] == 'final' else before), 'OpenClaw helper does not bind the captured checkout bytes')
    return plan, workload, streams, ledgers, before, after, {'provider': model['provider'], 'model': model['model']}


def observer_from_capture_documents(documents):
    """Build the observer of an ACP capture: tool events from the ACP stream of each turn; no native row is read."""
    plan, workload, streams, ledgers, before, after, model = _independent_capture(documents)
    observer = build_openclaw_acp_observer(workload=workload, stream_by_turn=streams, helper_by_turn=ledgers, before_sha256=sha(before),
                                           after_sha256=sha(after), session_key=plan['session_key'], gateway_model=model)
    _validate_observer(observer)
    return observer


def verify_state_receipt(receipt, before, after):
    """Check the whole-home receipt against its two inventories, or raise ValueError. Returns the classes."""
    from .hermes_state_evidence import HermesStateError, _canonical, inventory_sha256
    from .openclaw_state_capture import (
        STATE_SCHEMA, STATE_SCOPE, _statement, _strip_paths, classify_openclaw_changes, new_rollout_threads,
    )
    quiet = receipt.get('quiescence') if isinstance(receipt.get('quiescence'), dict) else {}
    digests = quiet.get('inventory_sha256') if isinstance(quiet.get('inventory_sha256'), list) else []
    rules = receipt.get('rules')
    _check(receipt.get('schema_version') == STATE_SCHEMA and receipt.get('scope') == STATE_SCOPE and receipt.get('turn') == 2
           and receipt.get('metadata_only_before_after') is True and receipt.get('preexisting_contents_opened') is False
           and receipt.get('shared_store_rows_read') == 'this session only' and isinstance(receipt.get('source_root'), str)
           and receipt['source_root'].endswith(STATE_ROOT_SUFFIX) and before.get('root') == receipt['source_root'],
           'OpenClaw whole-home receipt identity differs')
    _check(inventory_sha256(before) == receipt.get('before_inventory_sha256') and inventory_sha256(after) == receipt.get('after_inventory_sha256')
           and quiet.get('stable') is True and quiet.get('unchanged_after_copy') is True and quiet.get('reads') == len(digests)
           and len(digests) >= 2 and digests[-1] == digests[-2] == receipt['after_inventory_sha256'],
           'OpenClaw whole-home inventory or quiescence differs')
    _check(isinstance(rules, dict) and receipt.get('rules_sha256') == sha(_canonical(rules)), 'OpenClaw receipt does not bind its rule table')
    try:
        threads = new_rollout_threads(before, after, rules=rules)
        classes = classify_openclaw_changes(before, after, session_id=receipt.get('session_id'), thread_id=receipt.get('thread_id'),
                                            key_id=receipt.get('key_id'), acp_session_id=receipt.get('acp_session_id'),
                                            started_ns=receipt['started_ns'], rules=rules)
    except HermesStateError as error:
        raise ValueError(f'OpenClaw whole-home classification failed: {error}') from error
    recorded, accounting, store = receipt.get('classes', {}), receipt.get('accounting', {}), receipt.get('session_store', {})
    shared = sorted(row['relative_path'] for row in classes['shared_changed'])
    _check(receipt.get('new_rollout_threads') == threads == [receipt.get('thread_id')]
           and all(recorded.get(name) == classes[name] for name in ('session_owned', 'run_owned', 'directories_changed'))
           and _strip_paths(recorded.get('shared_changed', [])) == classes['shared_changed']
           and receipt.get('session_directories') == classes['session_directories']
           and accounting.get('changed_entries') == classes['changed_entries'] and accounting.get('unexplained') == []
           and accounting.get('every_changed_entry_accounted') is True
           and accounting.get('shared_entries_changed_content_not_read') == shared
           and accounting.get('statement') == _statement(classes, store),
           'OpenClaw whole-home classification differs from the receipt')
    return classes


def outside_files(receipt):
    """The files of the family that are outside the read: the session-owned files of the receipt, by path and size."""
    return [{'relative_path': row['relative_path'], 'size_bytes': row['size_bytes']}
            for row in receipt.get('artifacts', []) if row.get('class') == 'session_owned']


def family_findings(receipt):
    """What the receipt says about rows of the session that the export does not hold.

    Returns the limits of the family as text. They are stated in the packet
    context. Only reconciliation is resolved as an absence (``apply_openclaw_reconciliation_absence``); it rests on the rows, not on these limits.
    """
    notes = []
    for store in receipt.get('session_store', {}).get('stores', []):
        if not store.get('present'):
            notes.append(f"{store.get('store')}: not present")
            continue
        denied = len(store.get('tables_denied', []))
        inside = sum((row.get('rows_that_contain_an_id') or 0) - (row.get('rows_with_exact_match') or 0) for row in store.get('tables', [])
                     if not row.get('denied'))
        notes.append(f"{store['store']}: {store['selected_rows']} rows exported; {denied} tables never read (deny rule); "
                     f"{inside} rows hold an id only inside a longer value and are not exported")
    return notes


def apply_openclaw_reconciliation_absence(measurement, decoded):
    """Resolve ``attribution.reconciliation`` as ``native_absent`` when the rows hold no per-response usage records for a total.

    The rubric asks that the per-response usage records sum to the declared
    total. The store writes the total of a run in two places and holds zeros
    on the other assistant messages (see ``records_with_counts`` of the run
    totals). The sum equals the total only because one record is the total.
    This is the state of Hermes, whose rows hold no usage record that sums to
    a total. It is not a measured failure: no native value contradicts
    another. The decode must be clean and the packet bound (the capture
    assertion proves that the rows of every store naming the session are
    exported), else the row stays unresolved.
    """
    clean = decoded.get('status') == 'ok' and not decoded.get('diagnostics') and decoded.get('reconciliation') == [] and bool(decoded.get('run_totals'))
    rows = [dict(row) for row in measurement['metrics']]
    for row in rows:
        if clean and row['id'] == 'attribution.reconciliation' and row['state'] == 'unresolved' and row['correct'] == 0 and row['decoded_eligible'] == 0:
            row['state'] = 'native_absent'
    return {**measurement, 'metrics': rows}


def validate_openclaw_capture_assertion(contents, context, native_inventory):
    """Bind the packet to one whole-home ACP capture. Returns (complete family, complete root) = (True, False) or raises."""
    from .openclaw_state_capture import check_rows_name_only_test_session
    assertion = read_json(contents[context['capture_assertion_path']], 'OpenClaw assertion')
    _check(assertion.get('schema_version') == ASSERTION_SCHEMA and assertion.get('run_id') == context['run_id']
           and assertion.get('repetition') == context['repetition'] and context.get('observer_kind') == OBSERVER_KIND
           and assertion.get('native_inventory_sha256') == sha(contents['native/decode.json']), 'OpenClaw assertion identity or inventory mismatch')
    for name in ('workload.json', 'observer.json'):
        _check(assertion.get('input_sha256', {}).get(name) == sha(contents['inputs/' + name]), 'OpenClaw input binding mismatch')
    documents = {}
    for entry in assertion.get('capture_documents', []):
        name = entry.get('path') if isinstance(entry, dict) else None
        data = contents.get('inputs/capture/' + str(name))
        _check(isinstance(name, str) and '..' not in Path(name).parts and name not in documents and data is not None
               and sha(data) == entry.get('sha256') and len(data) == entry.get('size_bytes'), 'OpenClaw capture proof member mismatch')
        documents[name] = data
    _check(set(documents) == set(CAPTURE_DOCUMENTS)
           and {name for name in contents if name.startswith('inputs/capture/')} == {'inputs/capture/' + name for name in documents},
           'OpenClaw capture proof is not the closed document set')
    plan, workload, *_ = _independent_capture(documents)
    result, preflight = read_json(documents['capture-result.json'], 'capture result'), read_json(documents['preflight.json'], 'preflight')
    receipt, before, after = (read_json(documents[name], name) for name in (RECEIPT, BEFORE, AFTER))
    _check(plan['attempt_id'] == context['run_id'] and plan['repetition'] == context['repetition'] and preflight.get('ready') is True
           and preflight.get('version') == context['build'] == receipt.get('build') == plan.get('expected_version'),
           'OpenClaw receipts differ from the packet context')
    identity = {key: receipt.get(key) for key in ('session_id', 'thread_id', 'session_key', 'key_id', 'acp_session_id')}
    _check(receipt.get('attempt_id') == context['run_id'] and receipt.get('started_ns') == result.get('started_ns')
           and identity['session_id'] == result.get('session_id') and identity['thread_id'] == result.get('thread_id')
           and identity['session_key'] == plan['session_key'] and identity['key_id'] == plan['key_id']
           and identity['acp_session_id'] == result.get('acp_session_id') and isinstance(identity['session_id'], str),
           'OpenClaw whole-home receipt names another run or session')
    classes = verify_state_receipt(receipt, before, after)
    # The files named by an id of the session: the three files of the Codex thread. They are outside the read.
    owned = {row['relative_path']: row for row in receipt.get('artifacts', []) if row.get('class') == 'session_owned'}
    _check(set(owned) == set(classes['session_owned']) and all(row.get('owner_id') == 'codex_thread_id' for row in owned.values()),
           'OpenClaw session-owned files differ from the receipt, or a file is named by the session id (this contract reads rows only)')
    declared = {row.get('path'): row for row in native_inventory['artifacts']}
    names = {'capture/' + ROWS_FILE: 'rows_export', 'capture/' + SCHEMA_FILE: 'schema_export'}
    _check(set(declared) == set(names) and {'native/' + name for name in declared} == {name for name in contents if name.startswith('native/capture/')},
           'OpenClaw native inventory is not the row export and the schema export')
    store = receipt.get('session_store', {})
    for name, key in names.items():
        data, bound = contents['native/' + name], store.get(key, {})
        _check(sha(data) == declared[name].get('sha256') == bound.get('sha256') and len(data) == declared[name].get('size_bytes') == bound.get('size_bytes')
               and bound.get('path') == name[len('capture/'):], 'OpenClaw native file differs from the receipt')
    rows, schema = contents['native/capture/' + ROWS_FILE], contents['native/capture/' + SCHEMA_FILE]
    document = read_json(rows, 'row export')
    read, _others, _layout, _exceptions, _ = read_rows(rows, schema)
    _check(document.get('schema_version') == ROWS_SCHEMA and all(document.get(key) == identity[key] for key in identity),
           'OpenClaw row export names another session')
    try:
        check_rows_name_only_test_session(document, identity, rules=receipt['rules'])
    except ValueError as error:
        raise ValueError(f'OpenClaw row export holds a row of another session or of a denied table: {error}') from error
    summaries = [item for item in store.get('stores', []) if item.get('present')]
    _check([item.get('store') for item in summaries] == [item.get('store') for item in document['stores']]
           and all(item.get('schema_sha256') == exported.get('schema_sha256') == store_schema_sha256(schema, item['store'])
                   and {row['table']: row['selected_rows'] for row in item['tables'] if row['selected_rows']}
                   == {table['table']: len(table['rows']) for table in exported['tables']}
                   for item, exported in zip(summaries, document['stores']))
           and store.get('rows_of_other_sessions_exported') == 0 and store.get('rows_of_denied_tables_exported') == 0
           and store.get('temporary_copy', {}).get('deleted') is True, 'OpenClaw store schema, row counts or store copy receipt differs')
    _check(documents['turn-r2/workspace/fixture_project/bench_check.py'] == contents['runtime/fixtures/scenarios/survival-v1/workload/fixture_project/bench_check.py'],
           'OpenClaw helper differs from the frozen source')
    _check(canonical(observer_from_capture_documents(documents)) == contents['inputs/observer.json']
           and documents['workload-instance.json'] == contents['inputs/workload.json'], 'OpenClaw observer or workload differs from the capture')
    _check(context.get('required_companions') == REQUIRED_COMPANIONS, 'OpenClaw companion set differs from the contract')
    _check(context.get('root_repetitions') == [state_root_row(context['repetition'])], 'OpenClaw root row differs from the whole-home receipt')
    windows = read.get('session_windows', [])
    created = windows[0]['values'].get('created_at') if len(windows) == 1 else None
    _check(type(created) is int and datetime.fromtimestamp(created / 1000, tz=timezone.utc).date().isoformat() == context['collected_on'],
           'OpenClaw observation date differs from the native session start')
    # The stores hold other sessions and cannot be copied whole: the family is bound, the root is not complete.
    _check(context.get('complete_root') is False, 'OpenClaw root with shared stores cannot be declared complete')
    return True, False


def build_openclaw_replay_evidence(root, native, instance, context, common):
    """Decode the rows of the packet and build broad evidence for the family of the session."""
    root = Path(root)
    decoded = decode_openclaw_native(native, run_canary=instance.get('run_canary'))
    add_native_final_after_chain(decoded, instance)
    common = dict(common)
    names = ['inputs/capture/' + name for name in (RECEIPT, BEFORE, AFTER)]
    common['openclaw_root_locators'] = [{'id': name, 'sha256': sha((root / name).read_bytes())} for name in names]
    receipt = read_json((root / 'inputs/capture' / RECEIPT).read_bytes(), 'receipt')
    common['openclaw_outside_files'] = outside_files(receipt)
    common['strict_result_times'] = True
    return decoded, decoded, build_openclaw_format_evidence(decoded, decoded, context=context, common=common)


def remove_final_response_file(native, output, canary):
    """Selected-loss control: copy the native directory without the final response row. Returns the removed records."""
    native, output = Path(native), Path(output)
    inventory = read_json((native / 'decode.json').read_bytes(), 'OpenClaw loss inventory')
    removed = []
    for path in sorted(item for item in native.rglob('*') if item.is_file()):
        relative = path.relative_to(native).as_posix()
        data = path.read_bytes()
        if relative == 'capture/' + ROWS_FILE:
            data, removed = remove_final_response(data, canary)
            entry = next(row for row in inventory['artifacts'] if row['path'] == relative)
            entry.update(sha256=sha(data), size_bytes=len(data))
        if relative != 'decode.json':
            target = output / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
    (output / 'decode.json').write_bytes(canonical(inventory) + b'\n')
    return removed


__all__ = ['AGENT_STORE', 'ASSERTION_SCHEMA', 'CAPTURE_DOCUMENTS', 'OBSERVER_KIND', 'REQUIRED_COMPANIONS', 'build_openclaw_replay_evidence',
           'family_findings', 'observer_from_capture_documents', 'outside_files', 'remove_final_response_file', 'state_root_row',
           'validate_openclaw_capture_assertion', 'verify_state_receipt']
