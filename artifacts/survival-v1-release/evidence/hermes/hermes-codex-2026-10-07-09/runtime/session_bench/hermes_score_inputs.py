"""Replay inputs for a whole-home Hermes capture (receipt ``hermes-state-root-v1``).

Hermes runs in the operator's normal home. A session has no file of its own:
its native record is its rows in the shared store ``~/.hermes/state.db``. The
controller lists the whole Hermes home by metadata before the first turn and
after each turn, accounts for every changed entry, and exports the rows of
the session from a private copy of the store.

The family is the bound rows: the ``sessions`` row, the ``messages`` rows and
the ``session_model_usage`` rows that name the session id. The receipt proves
that no file of the home carries the session id and that every row which
holds the id in any column is exported. The family is complete in this sense.

The root is not complete. The store holds other sessions and cannot be copied
whole, so ``portable.complete_root`` is false. Two kinds of rows of the store
belong to the session and are not in the row export, because they do not name
its id: the ``system_prompts`` row that the session row names by
``system_prompt_hash``, and the rows of the search index (``messages_fts*``).
The system prompt row is a companion of the session row. It was read after
the capture on the owner's decision and is bound by content
(``system-prompt-row.json``): its hash is the SHA-256 of its text and equals
the hash in the captured session row. A packet without it has
``portable.companions`` false. The index rows were not read.

Absence rows (``native_absent``) rest on the bound rows, on the schema of the
store (which names every column of every table) and on the receipt. They are
accepted only by ``absence_findings``.

The observer is independent of the store: the submitted prompts, the final
text on stdout, the helper ledger that the frozen helper writes itself, and
the workspace copies. Hermes ``-z`` prints only the final text, so no tool
call is observed as such. The helper ledger gives three observed actions with
their results. The fourth is the edit: the workspace copies show that the
workload file changed during the second turn.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from .hermes_format_evidence import build_hermes_format_evidence
from .hermes_store_rows import (
    COLUMNS, PROMPT_FILE, ROWS_FILE, ROWS_SCHEMA, SCHEMA_FILE, STORE, TARGET, add_native_final_after_chain, decode_hermes_native,
    read_prompt_row, read_rows, remove_final_response, store_schema_sha256, usage_key_findings,
)
from .live_metric_comparator import _validate_observer
from .native_replay import canonical

ASSERTION_SCHEMA = 'session-bench-hermes-score-inputs-v1'
OBSERVER_KIND = 'hermes-capture-v1'
CAPTURE_SCHEMA = 'session-bench-hermes-survival-capture-v1'
# Captures of the corrected controller: ``chat -q --format stream-json`` with the terminal and file toolsets.
# The observer is built from the stdout stream of each turn.
STREAM_ASSERTION_SCHEMA = 'session-bench-hermes-score-inputs-v2'
STREAM_OBSERVER_KIND = 'hermes-stream-capture-v1'
STREAM_CAPTURE_SCHEMA = 'session-bench-hermes-survival-capture-v2'
RECEIPT, BEFORE, AFTER = 'r2-native-receipt.json', 'state-before.json', 'r2-state-after.json'
VERSION_EXTRACT = 'shared-store-extract/state-db-schema-version.json'
_TURN_DOCUMENTS = ('launch.json', 'exit.json', 'stdout.txt', 'stderr.txt', 'usage.json',
                   'workspace/fixture_project/.survival-observer.jsonl', 'workspace/fixture_project/checkout.py',
                   'workspace/fixture_project/bench_check.py')
CAPTURE_DOCUMENTS = ('plan.json', 'capture-result.json', 'preflight.json', 'workload-instance.json', 'workload-template.json',
                     'observer/prompt-r1.txt', 'observer/prompt-r2.txt', 'workspaces/before/fixture_project/checkout.py',
                     BEFORE, AFTER, RECEIPT) + tuple(f'turn-r{turn}/{name}' for turn in (1, 2) for name in _TURN_DOCUMENTS)
_STREAM_TURN_DOCUMENTS = ('launch.json', 'exit.json', 'stdout.jsonl', 'stderr.txt', 'stream-receipt.json',
                          'workspace/fixture_project/.survival-observer.jsonl', 'workspace/fixture_project/checkout.py',
                          'workspace/fixture_project/bench_check.py')
STREAM_CAPTURE_DOCUMENTS = ('plan.json', 'capture-result.json', 'preflight.json', 'workload-instance.json', 'workload-template.json',
                            'observer/prompt-r1.txt', 'observer/prompt-r2.txt', 'workspaces/before/fixture_project/checkout.py',
                            BEFORE, AFTER, RECEIPT, VERSION_EXTRACT) + tuple(
                                f'turn-r{turn}/{name}' for turn in (1, 2) for name in _STREAM_TURN_DOCUMENTS)
# The companion that the session row names by ``system_prompt_hash``. The capture does not hold it.
SYSTEM_PROMPT_COMPANION = 'capture/' + PROMPT_FILE
STATE_ROOT_SUFFIX = '/.hermes'
ROOT_LOCATOR = ('~/.hermes/state.db: the rows of the tables sessions, messages and session_model_usage whose session id is the one that '
                'the usage receipt of the run reports (sessions.id is the primary key; the other two tables reference it); '
                'no file of the Hermes home carries the session id (two metadata-only inventories of the whole home)')
_PHASES = ('inspect', 'baseline', 'final')
# A changed shared entry that the absence proof accepts unread: the store itself (its rows are bound), a
# directory, a digested subtree, and a process log under logs/.
_SIDECARS = ('-wal', '-shm', '-journal')
# Changed shared files that the absence proof accepts unread, by name, with the reason. They are process
# state of the harness, not stores of session records. The content was not read: this is a judgment, and
# the adapter document lists it. A file smaller than a session id is accepted without a name: it cannot name the session.
ACCEPTED_UNREAD_FILES = {
    'skills/.bundled_manifest': 'manifest of the bundled skills that Hermes syncs at start; equal size before and after',
    'spawn-ledger.json': 'ledger of the processes that Hermes started; a few hundred bytes',
    'runtime/active_sessions.json': 'register of running sessions; 15 bytes before and after',
}


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


def build_of(version_text):
    """The build name: the first line of the version text of the installed entry point."""
    return version_text.splitlines()[0].strip() if isinstance(version_text, str) and version_text.strip() else None


def _independent_capture(documents):
    """Check the independent receipts of one capture. No native row is opened.

    Returns (plan, workload, helper ledger of turn 2, final texts, launches, checkout before, checkout after).
    """
    plan, result, workload = (read_json(documents[name], name) for name in ('plan.json', 'capture-result.json', 'workload-instance.json'))
    run_id, session = plan.get('attempt_id'), result.get('session_id')
    _check(plan.get('schema_version') == result.get('schema_version') == CAPTURE_SCHEMA and plan.get('configuration_id') == 'hermes'
           and isinstance(run_id, str) and run_id and result.get('attempt_id') == workload.get('run_id') == run_id
           and isinstance(session, str) and session and result.get('status') == 'captured_pending_qualification'
           and result.get('model_submissions') == 2 and result.get('state_root_scope') == 'whole_hermes_home'
           and plan.get('provider') == result.get('provider') and plan.get('model') == result.get('model')
           and type(plan.get('repetition')) is int and plan['repetition'] in (1, 2, 3), 'Hermes capture identity or completion mismatch')
    _check(workload.get('run_canary') == 'SB_SURVIVAL_V1_RUN_' + run_id, 'Hermes workload canary mismatch')
    _check(sha(documents['workload-template.json']) == plan.get('workload_sha256'), 'Hermes workload template digest mismatch')
    turns, receipts, protected = workload.get('turns'), result.get('turns'), plan.get('protected_sha256')
    _check(isinstance(turns, list) and len(turns) == 2 and isinstance(receipts, list) and len(receipts) == 2
           and isinstance(protected, dict) and {'bench_check.py', 'checkout.py'} <= set(protected), 'Hermes requires two captured turns')
    before = documents['workspaces/before/fixture_project/checkout.py']
    _check(sha(before) == protected['checkout.py'], 'Hermes initial checkout digest mismatch')
    ledgers, finals, launches = [], [], []
    for number, (turn, receipt) in enumerate(zip(turns, receipts), 1):
        prefix = f'turn-r{number}/'
        launch, leave, usage = (read_json(documents[prefix + name], name) for name in ('launch.json', 'exit.json', 'usage.json'))
        prompt, stdout, stderr = documents[f'observer/prompt-r{number}.txt'], documents[prefix + 'stdout.txt'], documents[prefix + 'stderr.txt']
        argv = launch.get('argv')
        _check(turn.get('id') == f'turn-r{number}' and turn.get('revision') == f'r{number}' and isinstance(turn.get('text'), str)
               and prompt == turn['text'].encode(), 'Hermes submitted prompt mismatch')
        _check(isinstance(argv, list) and argv[-2:] == ['-z', turn['text']] and launch.get('prompt_sha256') == sha(prompt)
               and launch.get('provider') == plan['provider'] and launch.get('model') == plan['model'] and launch.get('cwd') == plan.get('workspace')
               and launch.get('session_id') == (None if number == 1 else session), 'Hermes launch binding mismatch')
        for flag, value in (('--provider', plan['provider']), ('--model', plan['model'])):
            _check(argv.count(flag) == 1 and argv[argv.index(flag) + 1] == value, 'Hermes launch identity argument mismatch')
        _check((argv.count('--resume') == 1 and argv[argv.index('--resume') + 1] == session) if number == 2 else '--resume' not in argv,
               'Hermes second turn does not resume the session of the first')
        _check(receipt.get('turn') == number and receipt.get('status') == 'completed' and receipt.get('returncode') == 0
               and leave.get('returncode') == 0 and stderr == b'', 'Hermes unsuccessful launch')
        for name, raw in (('stdout', stdout), ('stderr', stderr)):
            _check(receipt.get(name + '_sha256') == leave.get(name + '_sha256') == sha(raw), 'Hermes stream digest mismatch')
        _check(receipt.get('usage_sha256') == sha(documents[prefix + 'usage.json']) and usage.get('session_id') == session
               and usage.get('provider') == plan['provider'] and usage.get('model') == plan['model'] and usage.get('completed') is True,
               'Hermes usage receipt does not confirm the session and the route')
        text = stdout.decode('utf-8')
        text = text[:-1] if text.endswith('\n') else text   # the one terminal newline of the print mode
        canary = turn.get('response_canary')
        _check(isinstance(canary, str) and canary.startswith('SB_SURVIVAL_V1_RESPONSE_') and text.endswith(canary) and text.count(canary) == 1,
               'Hermes final stdout canary mismatch')
        finals.append(text)
        launches.append(launch)
        _check(sha(documents[prefix + 'workspace/fixture_project/bench_check.py']) == protected['bench_check.py'] == workload.get('helper', {}).get('sha256'),
               'Hermes helper changed')
        rows = [read_json(line, 'helper ledger') for line in documents[prefix + 'workspace/fixture_project/.survival-observer.jsonl'].splitlines() if line.strip()]
        _check(len(rows) == (2 if number == 1 else 3), 'Hermes missing or extra helper execution')
        for row, phase in zip(rows, _PHASES):
            nonce = workload['helper']['nonces'].get(phase)
            marker = f'SB_SURVIVAL_V1_HELPER_{phase.upper()}_{nonce} '
            _check(isinstance(nonce, str) and nonce and isinstance(row, dict) and row.get('schema_version') == '1.0-survival-helper-ledger'
                   and row.get('phase') == phase and row.get('helper_nonce') == nonce and row.get('id') == f'helper-{phase}-{nonce}'
                   and row.get('run_canary') == workload['run_canary'] and row.get('argv') == ['python3', 'bench_check.py', phase]
                   and row.get('cwd') == 'fixture_project' and type(row.get('exit_code')) is int
                   and row['exit_code'] == (1 if phase == 'baseline' else 0) and isinstance(row.get('output'), str)
                   and row['output'].startswith(marker), 'Hermes helper identity or outcome mismatch')
        ledgers.append(rows)
    _check(ledgers[0] == ledgers[1][:2], 'Hermes helper prefix changed across turns')
    middle, after = (documents[f'turn-r{number}/workspace/fixture_project/checkout.py'] for number in (1, 2))
    _check(middle == before and middle != after, 'Hermes checkout revision boundary mismatch')
    for row in ledgers[1]:
        _check(row.get('checkout_sha256') == sha(after if row['phase'] == 'final' else before), 'Hermes helper does not bind the captured checkout bytes')
    return plan, workload, ledgers[1], finals, launches, before, after


def observer_from_capture_documents(documents):
    """Build the observer from the independent capture documents; no native row is read.

    Observed: the two submitted prompts; the final text of each turn on
    stdout; three helper processes from the helper ledger (argv, exit code,
    output line); the changed workload file from the workspace copies. The
    edit action is observed through its effect: the file is equal before and
    after the first turn and differs after the second. Its result is the
    change itself (success). The model and the provider come from the launch
    arguments, confirmed by the usage receipt.
    """
    plan, workload, ledger, finals, launches, before, after = _independent_capture(documents)
    session = read_json(documents['capture-result.json'], 'capture result')['session_id']
    events, relations = [], []

    def event(kind, identity, fields, source, metrics):
        events.append({'id': identity, 'sequence': len(events) + 1, 'population_role': 'primary_scored', 'kind': kind,
                       'session_id': session, 'fields': fields, 'metric_ids': metrics, 'source': source})

    def relation(kind, source, target):
        relations.append({'id': f'relation-{len(relations) + 1}', 'sequence': len(relations) + 1, 'kind': kind, 'from_id': source, 'to_id': target})

    def helper(row, turn):
        phase = row['phase']
        event('action', 'action-' + phase, {'turn_id': turn, 'argv': row['argv'], 'cwd': row['cwd'],
                                            'action_kind': 'inspect' if phase == 'inspect' else 'test'}, 'copied_helper_ledger',
              ['work.actions', 'causal.action_result'])
        event('result', 'result-' + phase, {'action_id': 'action-' + phase, 'turn_id': turn, 'helper_nonce': row['helper_nonce'],
                                            'output': row['output'], 'exit_code': row['exit_code'],
                                            'status': 'success' if row['exit_code'] == 0 else 'failure'}, 'copied_helper_ledger',
              ['work.results', 'causal.action_result'])
        relation('action_result', 'action-' + phase, 'result-' + phase)

    for number, turn in enumerate(workload['turns'], 1):
        event('user_turn', turn['id'], {'turn_id': turn['id'], 'revision': turn['revision'], 'role': 'user', 'text': turn['text'],
                                        'run_canary': workload['run_canary']}, 'copied_submitted_prompt',
              ['work.submitted_turns', f'revision.r{number}', 'revision.r1_r2_order'])
        if number == 1:
            for row in ledger[:2]:
                helper(row, turn['id'])
        else:
            event('action', 'action-edit', {'turn_id': turn['id'], 'name': 'file_edit', 'target': TARGET, 'action_kind': 'edit'},
                  'copied_workspace_bytes', ['work.actions', 'causal.action_result'])
            event('result', 'result-edit', {'action_id': 'action-edit', 'turn_id': turn['id'], 'status': 'success'},
                  'copied_workspace_bytes', ['work.results', 'causal.action_result'])
            relation('action_result', 'action-edit', 'result-edit')
            event('file_change', 'change-checkout', {'path': TARGET, 'before_sha256': sha(before), 'after_sha256': sha(after),
                                                     'turn_id': turn['id'], 'action_id': 'action-edit'}, 'copied_workspace_bytes',
                  ['work.changed_files'])
            helper(ledger[2], turn['id'])
        launch = launches[number - 1]
        event('assistant_response', f'response-r{number}', {'turn_id': turn['id'], 'role': 'assistant', 'status': 'completed',
              'text': finals[number - 1], 'canary': turn['response_canary'], 'model_id': launch['model'],
              'configuration': {'provider': launch['provider']}}, 'copied_stdout_and_launch',
              ['work.visible_responses', 'causal.turn_response', 'attribution.model_config', 'attribution.usage', 'attribution.token_semantics'])
        relation('turn_response', turn['id'], f'response-r{number}')
    relation('supersedes', 'turn-r1', 'turn-r2')
    relation('final_after', 'turn-r2', 'action-final')
    observer = {'schema_version': '1.0-survival-observer', 'protocol_version': '1.0-survival', 'scenario_id': 'survival-v1-repair',
                'run_id': workload['run_id'], 'independent': True,
                'method': ('exact submitted prompts; final text of each turn on stdout; helper ledger written by the frozen helper; '
                           'workspace copies before and after each turn; no tool call stream exists in the print mode; no native input'),
                'events': events, 'relations': relations}
    _validate_observer(observer)
    return observer


def _independent_stream_capture(documents):
    """Check the independent receipts of one stream capture. No native row is opened.

    Returns (plan, workload, helper ledger bytes of turn 2, launches, checkout before, checkout after, stdout by turn).
    """
    from .hermes_stream_observer import stream_summary
    plan, result, workload = (read_json(documents[name], name) for name in ('plan.json', 'capture-result.json', 'workload-instance.json'))
    run_id, session = plan.get('attempt_id'), result.get('session_id')
    _check(plan.get('schema_version') == result.get('schema_version') == STREAM_CAPTURE_SCHEMA and plan.get('configuration_id') == 'hermes'
           and isinstance(run_id, str) and run_id and result.get('attempt_id') == workload.get('run_id') == run_id
           and isinstance(session, str) and session and result.get('status') == 'captured_pending_qualification'
           and result.get('model_submissions') == 2 and result.get('state_root_scope') == 'whole_hermes_home'
           and plan.get('provider') == result.get('provider') and plan.get('model') == result.get('model')
           and plan.get('toolsets') == ['terminal', 'file'] and plan.get('stdout_format') == 'stream-json' and plan.get('approval') == '--yolo'
           and type(plan.get('repetition')) is int and plan['repetition'] in (1, 2, 3), 'Hermes stream capture identity or completion mismatch')
    _check(workload.get('run_canary') == 'SB_SURVIVAL_V1_RUN_' + run_id, 'Hermes workload canary mismatch')
    _check(sha(documents['workload-template.json']) == plan.get('workload_sha256'), 'Hermes workload template digest mismatch')
    turns, receipts, protected = workload.get('turns'), result.get('turns'), plan.get('protected_sha256')
    _check(isinstance(turns, list) and len(turns) == 2 and isinstance(receipts, list) and len(receipts) == 2
           and isinstance(protected, dict) and {'bench_check.py', 'checkout.py'} <= set(protected), 'Hermes requires two captured turns')
    before = documents['workspaces/before/fixture_project/checkout.py']
    _check(sha(before) == protected['checkout.py'], 'Hermes initial checkout digest mismatch')
    ledgers, launches, streams = [], [], {}
    for number, (turn, receipt) in enumerate(zip(turns, receipts), 1):
        prefix = f'turn-r{number}/'
        launch, leave, stream = (read_json(documents[prefix + name], name) for name in ('launch.json', 'exit.json', 'stream-receipt.json'))
        prompt, stdout, stderr = documents[f'observer/prompt-r{number}.txt'], documents[prefix + 'stdout.jsonl'], documents[prefix + 'stderr.txt']
        argv = launch.get('argv')
        _check(turn.get('id') == f'turn-r{number}' and turn.get('revision') == f'r{number}' and isinstance(turn.get('text'), str)
               and prompt == turn['text'].encode(), 'Hermes submitted prompt mismatch')
        _check(isinstance(argv, list) and argv[-2:] == ['-q', turn['text']] and 'chat' in argv and launch.get('prompt_sha256') == sha(prompt)
               and launch.get('provider') == plan['provider'] and launch.get('model') == plan['model'] and launch.get('cwd') == plan.get('workspace')
               and launch.get('toolsets') == plan['toolsets'] and launch.get('session_id') == (None if number == 1 else session),
               'Hermes launch binding mismatch')
        for flag, value in (('--provider', plan['provider']), ('--model', plan['model']), ('--toolsets', 'terminal,file'), ('--format', 'stream-json')):
            _check(argv.count(flag) == 1 and argv[argv.index(flag) + 1] == value, 'Hermes launch argument mismatch')
        _check((argv.count('--resume') == 1 and argv[argv.index('--resume') + 1] == session) if number == 2 else '--resume' not in argv,
               'Hermes second turn does not resume the session of the first')
        _check(receipt.get('turn') == number and receipt.get('status') == 'completed' and receipt.get('returncode') == 0
               and leave.get('returncode') == 0, 'Hermes unsuccessful launch')
        for name, raw in (('stdout', stdout), ('stderr', stderr)):
            _check(receipt.get(name + '_sha256') == leave.get(name + '_sha256') == sha(raw), 'Hermes stream digest mismatch')
        summary = stream_summary(stdout, f'stdout R{number}')
        _check(summary['session_id'] == session == stream.get('session_id') and summary['model'] == plan['model'] == stream.get('model')
               and stream.get('stdout_sha256') == sha(stdout), 'Hermes stream does not confirm the session and the model')
        _check(sha(documents[prefix + 'workspace/fixture_project/bench_check.py']) == protected['bench_check.py'] == workload.get('helper', {}).get('sha256'),
               'Hermes helper changed')
        rows = [read_json(line, 'helper ledger') for line in documents[prefix + 'workspace/fixture_project/.survival-observer.jsonl'].splitlines() if line.strip()]
        _check(len(rows) == (2 if number == 1 else 3), 'Hermes missing or extra helper execution')
        for row, phase in zip(rows, _PHASES):
            nonce = workload['helper']['nonces'].get(phase)
            marker = f'SB_SURVIVAL_V1_HELPER_{phase.upper()}_{nonce} '
            _check(isinstance(nonce, str) and nonce and isinstance(row, dict) and row.get('schema_version') == '1.0-survival-helper-ledger'
                   and row.get('phase') == phase and row.get('helper_nonce') == nonce and row.get('id') == f'helper-{phase}-{nonce}'
                   and row.get('run_canary') == workload['run_canary'] and row.get('argv') == ['python3', 'bench_check.py', phase]
                   and row.get('cwd') == 'fixture_project' and type(row.get('exit_code')) is int
                   and row['exit_code'] == (1 if phase == 'baseline' else 0) and isinstance(row.get('output'), str)
                   and row['output'].startswith(marker), 'Hermes helper identity or outcome mismatch')
        ledgers.append(rows)
        launches.append(launch)
        streams[number] = stdout
    _check(ledgers[0] == ledgers[1][:2], 'Hermes helper prefix changed across turns')
    middle, after = (documents[f'turn-r{number}/workspace/fixture_project/checkout.py'] for number in (1, 2))
    _check(middle == before and middle != after, 'Hermes checkout revision boundary mismatch')
    for row in ledgers[1]:
        _check(row.get('checkout_sha256') == sha(after if row['phase'] == 'final' else before), 'Hermes helper does not bind the captured checkout bytes')
    return plan, workload, documents['turn-r2/workspace/fixture_project/.survival-observer.jsonl'], launches, before, after, streams


def stream_observer_from_capture_documents(documents):
    """Build the observer of a stream capture: tool events from the stdout stream of each turn; no native row is read."""
    from .hermes_stream_observer import build_hermes_stream_observer
    _, workload, ledger, launches, before, after, streams = _independent_stream_capture(documents)
    return build_hermes_stream_observer(workload=workload, stdout_by_turn=streams, helper_document=ledger, before_sha256=sha(before),
                                        after_sha256=sha(after), launches=launches)


def scored_pool(decoded, observer):
    """The native facts without the calls that pair with an observed unscored action.

    Rule for unscored native actions. The stream and the rows share no call
    id. In one turn, an observed unscored call (a read, a search) pairs with
    the first native call of the same tool name and the same arguments that
    no earlier observed call took. A paired native call, its result and their
    relation leave the scored population. A native call that the observer did
    not see stays in the pool and enlarges the denominator.
    """
    from .live_metric_comparator import _match_many, _match_turn, _native_id, _observer_events
    events = observer['events']
    _, _, matched, _ = _match_many(_observer_events(events, 'user_turn'), decoded['turns'], _match_turn)
    turn_map = {_native_id(candidate): expected for expected, candidate in matched.items()}
    waiting = [(event['fields'].get('turn_id'), event['fields'].get('name'), canonical(event['fields'].get('input')))
               for event in events if event['kind'] == 'action' and event['population_role'] == 'unscored']
    dropped = set()
    for action in decoded['actions']:
        if 'parent_call_id' in action:
            continue
        key = (turn_map.get(action.get('turn_id')), action.get('name'), canonical(action.get('input')))
        if key in waiting:
            waiting.remove(key)
            dropped.add(action['id'])
    actions = [row for row in decoded['actions'] if row['id'] not in dropped]
    results = [row for row in decoded['results'] if row['action_id'] not in dropped]
    relations = [row for row in decoded['relations'] if row['kind'] != 'action_result' or row['from_id'] not in dropped]
    return {**decoded, 'actions': actions, 'results': results, 'relations': relations}


def verify_state_receipt(receipt, before, after):
    """Check the whole-home receipt against its two inventories, or raise ValueError. Returns the classes."""
    from .hermes_state_evidence import (
        STATE_SCHEMA, STATE_SCOPE, HermesStateError, _canonical, _statement, _strip_paths, carries_session_id,
        classify_hermes_changes, inventory_sha256,
    )
    quiet = receipt.get('quiescence') if isinstance(receipt.get('quiescence'), dict) else {}
    digests = quiet.get('inventory_sha256') if isinstance(quiet.get('inventory_sha256'), list) else []
    rules, session = receipt.get('rules'), receipt.get('session_id')
    _check(receipt.get('schema_version') == STATE_SCHEMA and receipt.get('scope') == STATE_SCOPE and receipt.get('turn') == 2
           and receipt.get('metadata_only_before_after') is True and receipt.get('preexisting_contents_opened') is False
           and receipt.get('shared_store_rows_read') == 'this session only' and isinstance(receipt.get('source_root'), str)
           and receipt['source_root'].endswith(STATE_ROOT_SUFFIX) and before.get('root') == receipt['source_root'],
           'Hermes whole-home receipt identity differs')
    _check(inventory_sha256(before) == receipt.get('before_inventory_sha256') and inventory_sha256(after) == receipt.get('after_inventory_sha256')
           and quiet.get('stable') is True and quiet.get('unchanged_after_copy') is True and quiet.get('reads') == len(digests)
           and len(digests) >= 2 and digests[-1] == digests[-2] == receipt['after_inventory_sha256'],
           'Hermes whole-home inventory or quiescence differs')
    _check(isinstance(rules, dict) and receipt.get('rules_sha256') == sha(_canonical(rules)), 'Hermes receipt does not bind its rule table')
    try:
        classes = classify_hermes_changes(before, after, session_id=session, started_ns=receipt['started_ns'], rules=rules)
    except HermesStateError as error:
        raise ValueError(f'Hermes whole-home classification failed: {error}') from error
    recorded, accounting = receipt.get('classes', {}), receipt.get('accounting', {})
    for row in recorded.get('shared_changed', []):
        folders, paths = set(row.get('changed_directories', [])), row.get('changed_paths')
        parents = {path.rsplit('/', 1)[0] for path in paths or []}
        _check(paths is None or (not any(carries_session_id(path, session) for path in paths) and parents <= folders
                                 and all(folder in parents or folder in paths for folder in folders)),
               'Hermes changed paths of a digested subtree differ from its directory digests')
    shared = sorted(row['relative_path'] for row in classes['shared_changed'])
    _check(all(recorded.get(name) == classes[name] for name in ('session_owned', 'run_owned', 'directories_changed'))
           and _strip_paths(recorded.get('shared_changed', [])) == classes['shared_changed']
           and receipt.get('session_directories') == classes['session_directories']
           and accounting.get('changed_entries') == classes['changed_entries'] and accounting.get('unexplained') == []
           and accounting.get('every_changed_entry_accounted') is True
           and accounting.get('shared_entries_changed_content_not_read') == shared
           and accounting.get('session_store_rows_read') == list(rules['session_stores'])
           and accounting.get('statement') == _statement(classes, receipt.get('session_store', {})),
           'Hermes whole-home classification differs from the receipt')
    return classes


def absence_findings(receipt, classes, tables):
    """What the receipt and the bound rows prove about records of the session outside the row export.

    Returns ``{'proved': bool, 'reasons': [...]}``. ``proved`` is true only
    when: no file of the home carries the session id and no new run file
    exists; every changed shared file is the store itself, a process log
    under ``logs/``, a file of ``ACCEPTED_UNREAD_FILES`` or a file smaller
    than the session id (every other changed entry is a directory or a
    digested subtree); the store is ``state.db`` alone; every table of the store was
    scanned; no join candidate was left out; in every table the number of
    rows that hold the id anywhere equals the number of rows with an exact
    match (so no unread row names the session inside a longer value); the
    exported rows are of the three known tables and their counts equal the
    receipt. Anything else proves nothing.
    """
    reasons = []
    if classes['session_owned'] or classes['run_owned'] or classes['session_directories']:
        reasons.append('a file of the home carries the session id or is a new run file; this contract reads rows only')
    session = receipt.get('session_id') if isinstance(receipt.get('session_id'), str) else ''
    for row in classes['shared_changed']:
        path, kind = row['relative_path'], row['kind']
        store_file = path == STORE or any(path == STORE + suffix for suffix in _SIDECARS)
        size = (row.get('after') or {}).get('size_bytes')
        too_small = kind == 'file' and type(size) is int and size < len(session)
        if not (store_file or kind in ('directory', 'directory-digest') or (kind == 'file' and path.startswith('logs/'))
                or (kind == 'file' and path in ACCEPTED_UNREAD_FILES) or too_small):
            reasons.append(f'a changed shared file was not read: {path}')
    store = receipt.get('session_store', {})
    summaries = store.get('stores') if isinstance(store.get('stores'), list) else []
    if len(summaries) != 1 or summaries[0].get('store') != STORE or store.get('rows_of_other_sessions_exported') != 0:
        return {'proved': False, 'reasons': reasons + ['the receipt does not bind the rows of state.db alone']}
    summary = summaries[0]
    if summary.get('tables_not_scanned') != [] or summary.get('join_candidates_not_exported') != []:
        reasons.append('a table was not scanned or a join candidate was not exported')
    counts = {}
    for row in summary.get('tables', []):
        if row.get('rows_that_contain_the_id') != row.get('rows_with_exact_match'):
            reasons.append(f"{row.get('table')}: a row holds the id inside a longer value")
        if row.get('selected_rows'):
            counts[row['table']] = row['selected_rows']
    if set(counts) - set(COLUMNS) or counts != {name: len(rows) for name, rows in tables.items() if rows}:
        reasons.append('the exported rows differ from the receipt or name a table outside the contract')
    return {'proved': not reasons, 'reasons': sorted(set(reasons))}


def required_companions(tables):
    """Companions of the bound rows: the schema export, and the system prompt row when the session row names one."""
    names = ['capture/' + SCHEMA_FILE]
    sessions = tables.get('sessions', [])
    if any(row['values'].get('system_prompt_hash') is not None for row in sessions):
        names.append(SYSTEM_PROMPT_COMPANION)
    return sorted(names)


def validate_hermes_capture_assertion(contents, context, native_inventory):
    """Bind the packet to one whole-home capture. Returns (complete family, complete root) = (True, False) or raises."""
    from .hermes_state_evidence import check_rows_name_only_session
    assertion = read_json(contents[context['capture_assertion_path']], 'Hermes assertion')
    stream = context.get('observer_kind') == STREAM_OBSERVER_KIND
    _check(assertion.get('schema_version') == (STREAM_ASSERTION_SCHEMA if stream else ASSERTION_SCHEMA) and assertion.get('run_id') == context['run_id']
           and assertion.get('repetition') == context['repetition'] and context.get('observer_kind') in (OBSERVER_KIND, STREAM_OBSERVER_KIND)
           and assertion.get('native_inventory_sha256') == sha(contents['native/decode.json']), 'Hermes assertion identity or inventory mismatch')
    for name in ('workload.json', 'observer.json'):
        _check(assertion.get('input_sha256', {}).get(name) == sha(contents['inputs/' + name]), 'Hermes input binding mismatch')
    documents = {}
    for entry in assertion.get('capture_documents', []):
        name = entry.get('path') if isinstance(entry, dict) else None
        data = contents.get('inputs/capture/' + str(name))
        _check(isinstance(name, str) and '..' not in Path(name).parts and name not in documents and data is not None
               and sha(data) == entry.get('sha256') and len(data) == entry.get('size_bytes'), 'Hermes capture proof member mismatch')
        documents[name] = data
    _check(set(documents) in ((set(STREAM_CAPTURE_DOCUMENTS),) if stream else (set(CAPTURE_DOCUMENTS), set(CAPTURE_DOCUMENTS) | {VERSION_EXTRACT}))
           and {name for name in contents if name.startswith('inputs/capture/')} == {'inputs/capture/' + name for name in documents},
           'Hermes capture proof is not the closed document set')
    plan, workload, *_ = (_independent_stream_capture if stream else _independent_capture)(documents)
    result, preflight = read_json(documents['capture-result.json'], 'capture result'), read_json(documents['preflight.json'], 'preflight')
    receipt, before, after = (read_json(documents[name], name) for name in (RECEIPT, BEFORE, AFTER))
    session = result['session_id']
    _check(plan['attempt_id'] == context['run_id'] and plan['repetition'] == context['repetition'] and preflight.get('ready') is True
           and build_of(preflight.get('version')) == context['build'] and build_of(receipt.get('build')) == context['build'],
           'Hermes receipts differ from the packet context')
    _check(receipt.get('attempt_id') == context['run_id'] and receipt.get('session_id') == session
           and receipt.get('started_ns') == result.get('started_ns'), 'Hermes whole-home receipt names another run or session')
    classes = verify_state_receipt(receipt, before, after)
    _check(not classes['session_owned'] and not classes['run_owned'] and receipt.get('artifacts') == [],
           'Hermes capture holds session files; this contract reads the rows of the store only')
    declared = {row.get('path'): row for row in native_inventory['artifacts']}
    names = {'capture/' + ROWS_FILE: 'rows_export', 'capture/' + SCHEMA_FILE: 'schema_export'}
    _check(set(declared) in ((set(names) | {SYSTEM_PROMPT_COMPANION},) if stream else (set(names), set(names) | {SYSTEM_PROMPT_COMPANION}))
           and {'native/' + name for name in declared} == {name for name in contents if name.startswith('native/capture/')},
           'Hermes native inventory is not the row export, the schema export and the system prompt row')
    store = receipt.get('session_store', {})
    for name, key in names.items():
        data, bound = contents['native/' + name], store.get(key, {})
        _check(sha(data) == declared[name].get('sha256') == bound.get('sha256') and len(data) == declared[name].get('size_bytes') == bound.get('size_bytes')
               and bound.get('path') == name[len('capture/'):], 'Hermes native file differs from the receipt')
    rows, schema = contents['native/capture/' + ROWS_FILE], contents['native/capture/' + SCHEMA_FILE]
    document = read_json(rows, 'row export')
    tables, _, _ = read_rows(rows, schema)
    _check(document.get('schema_version') == ROWS_SCHEMA and document.get('session_id') == session, 'Hermes row export names another session')
    if SYSTEM_PROMPT_COMPANION in declared:
        # The prompt row was read after the capture. It is bound by content: its hash is the SHA-256 of its
        # text and equals the hash that the captured session row names.
        data = contents['native/' + SYSTEM_PROMPT_COMPANION]
        _check(sha(data) == declared[SYSTEM_PROMPT_COMPANION].get('sha256') and len(data) == declared[SYSTEM_PROMPT_COMPANION].get('size_bytes')
               and len(tables.get('sessions', [])) == 1, 'Hermes system prompt row differs from the inventory')
        try:
            read_prompt_row(data, tables['sessions'][0]['values'].get('system_prompt_hash'))
        except ValueError as error:
            raise ValueError(f'Hermes system prompt row is not bound to the session row: {error}') from error
    try:
        check_rows_name_only_session(document, session)
    except ValueError as error:
        raise ValueError(f'Hermes row export holds a row of another session: {error}') from error
    summaries = store.get('stores') if isinstance(store.get('stores'), list) else []
    _check(len(summaries) == 1 and summaries[0].get('schema_sha256') == document['stores'][0].get('schema_sha256') == store_schema_sha256(schema)
           and store.get('temporary_copy', {}).get('deleted') is True, 'Hermes store schema or store copy receipt differs')
    findings = absence_findings(receipt, classes, tables)
    _check(not any('differ from the receipt' in reason for reason in findings['reasons']), 'Hermes exported rows differ from the receipt counts')
    _check(documents['turn-r2/workspace/fixture_project/bench_check.py'] == contents['runtime/fixtures/scenarios/survival-v1/workload/fixture_project/bench_check.py'],
           'Hermes helper differs from the frozen source')
    _check(canonical((stream_observer_from_capture_documents if stream else observer_from_capture_documents)(documents)) == contents['inputs/observer.json']
           and documents['workload-instance.json'] == contents['inputs/workload.json'], 'Hermes observer or workload differs from the capture')
    _check(context.get('required_companions') == required_companions(tables), 'Hermes companion set differs from the bound rows')
    _check(context.get('root_repetitions') == [state_root_row(context['repetition'])], 'Hermes root row differs from the whole-home receipt')
    started = tables['sessions'][0]['values'].get('started_at') if len(tables.get('sessions', [])) == 1 else None
    _check(isinstance(started, (int, float)) and not isinstance(started, bool)
           and datetime.fromtimestamp(started, tz=timezone.utc).date().isoformat() == context['collected_on'],
           'Hermes observation date differs from the native session start')
    if VERSION_EXTRACT in documents:
        from .hermes_store_rows import read_version_extract
        extract = read_json(documents[VERSION_EXTRACT], 'version extract')
        _check(extract.get('schema_sha256') == store_schema_sha256(schema), 'Hermes version extract names another store schema')
        read_version_extract(documents[VERSION_EXTRACT], read_json(schema, 'schema export')['stores'][0])
    # The store holds other sessions and cannot be copied whole: the family is complete, the root is not.
    _check(context.get('complete_root') is False, 'Hermes root with a shared store cannot be declared complete')
    return True, False


def apply_hermes_native_absence(measurement, decoded, findings, usage_findings):
    """Resolve the rows whose native record is absent, from evidence in hand.

    The root is not complete, so the rubric path is the complete id-named
    family (no file) plus the bound rows of the shared store that names the
    session. The proof needs a clean decode under the contract, an absence
    scan of the rows without a finding, and a receipt that proves that no
    other row or file names the session (``absence_findings``).

    Then: the rows hold no usage record of a response or a turn (usage, token
    semantics), so there is nothing to sum to the session total
    (reconciliation); they hold no file-change record (changed file); and
    they hold no edit record that could stand before the final test (final
    after R2). Anything less stays unresolved.
    """
    rows = [dict(row) for row in measurement['metrics']]
    proved = (decoded.get('status') == 'ok' and not decoded.get('diagnostics') and usage_findings == []
              and isinstance(findings, dict) and findings.get('proved') is True)
    absent = {'attribution.usage', 'attribution.token_semantics', 'attribution.reconciliation'}
    if not decoded.get('file_changes'):
        absent.add('work.changed_files')
    if not any(row.get('action_kind') == 'edit' for row in decoded.get('actions', [])):
        absent.add('revision.final_after_r2')
    usage = next((row for row in rows if row['id'] == 'attribution.usage'), {})
    if proved:
        for row in rows:
            if row['id'] in absent and row['state'] == 'unresolved' and row['correct'] == 0 and row['decoded_eligible'] == 0:
                row['state'] = 'native_absent'
            elif (row['id'] == 'attribution.token_semantics' and row['state'] == 'unresolved' and row['correct'] == 0
                  and row['decoded_eligible'] > 0 and usage.get('correct', 0) > 0):
                # The joined usage record exists and holds no cache-read and no cache-write key; the cache keys
                # of the bound rows sit only on the session totals. The record stays in the decoded population.
                row['state'] = 'native_absent'
    return {**measurement, 'metrics': rows}


def packet_native_absence(measurement, decoded, root, native):
    """Replay entry point: read the receipt, the inventories and the rows of this packet."""
    root, native = Path(root), Path(native)
    try:
        receipt, before, after = (read_json((root / 'inputs/capture' / name).read_bytes(), name) for name in (RECEIPT, BEFORE, AFTER))
        rows, schema = (native / 'capture' / ROWS_FILE).read_bytes(), (native / 'capture' / SCHEMA_FILE).read_bytes()
        tables, _, _ = read_rows(rows, schema)
        findings = absence_findings(receipt, verify_state_receipt(receipt, before, after), tables)
        usage_findings = usage_key_findings(rows, schema)
    except (ValueError, OSError, KeyError, TypeError):
        return measurement
    return apply_hermes_native_absence(measurement, decoded, findings, usage_findings)


def build_hermes_replay_evidence(root, native, instance, context, common):
    """Decode the rows of the packet and build broad evidence for the bound rows."""
    root = Path(root)
    extract = root / 'inputs/capture' / VERSION_EXTRACT
    decoded = decode_hermes_native(native, run_canary=instance.get('run_canary'),
                                   version_extract=extract.read_bytes() if extract.is_file() else None)
    add_native_final_after_chain(decoded, instance)
    common = dict(common)
    names = ['inputs/capture/' + name for name in (RECEIPT, BEFORE, AFTER)]
    common['hermes_root_locators'] = [{'id': name, 'sha256': sha((root / name).read_bytes())} for name in names]
    if extract.is_file():
        # The declared version is read from the version extract, so the version rows cite that file.
        common['hermes_version_locator'] = {'id': 'inputs/capture/' + VERSION_EXTRACT, 'sha256': sha(extract.read_bytes())}
    projected = decoded
    if context.get('observer_kind') == STREAM_OBSERVER_KIND:
        projected = scored_pool(decoded, read_json((root / 'inputs/observer.json').read_bytes(), 'observer'))
        common['strict_result_times'] = True
    return decoded, projected, build_hermes_format_evidence(decoded, projected, context=context, common=common)


def remove_final_response_file(native, output, canary):
    """Selected-loss control: copy the native directory without the final response row. Returns the removed records."""
    native, output = Path(native), Path(output)
    inventory = read_json((native / 'decode.json').read_bytes(), 'Hermes loss inventory')
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
