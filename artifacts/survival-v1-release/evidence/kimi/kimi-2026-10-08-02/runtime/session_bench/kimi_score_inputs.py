"""Replay inputs for a Kimi Code capture (controller ``session_bench.kimi_survival_capture``).

Kimi runs in an isolated home: ``KIMI_CODE_HOME`` is a new directory that
holds only a config file before the first turn. The controller lists the
whole home with a SHA-256 of every file before the first launch and after
each turn, and copies the session directory
``sessions/<workspace-id>/<session-id>/`` after each turn.

The native root of the row is that session directory. The packet holds every
file of it. The read is the wire log and the state file
(``session_bench.kimi_wire_records``).

Beside the session directory the home holds files that the controller listed
and did not copy. Three of them name the session and hold only ids, paths and
times: ``session_index.jsonl``, ``workspaces.json`` and
``file-history/<workspace-id>``. Their bytes are rebuilt from values of the
capture and proved by the SHA-256 of the listing (``home_index_proof``). The
other files are named in the capture assertion with their sizes
(``home_files_not_copied``); see the adapter document.

The observer is independent of the native files: the submitted prompts (the
``-p`` argument of each launch receipt), the stdout stream of each turn (tool
calls with arguments, results, the final text), the helper ledger that the
frozen helper writes itself, and the workspace copies.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re

from .kimi_format_evidence import build_kimi_format_evidence
from .kimi_stream_observer import KimiStreamError, build_kimi_stream_observer, stream_summary
from .kimi_wire_records import (
    SESSION_DIR, WIRE, STATE, add_native_final_after_chain, decode_kimi_native, read_wire, remove_final_response,
)
from .live_metric_comparator import _validate_observer
from .native_replay import canonical

ASSERTION_SCHEMA = 'session-bench-kimi-score-inputs-v1'
HOME_PROOF_SCHEMA = 'session-bench-kimi-home-index-proof-v1'
HOME_PROOF = 'home-index.json'
OBSERVER_KIND = 'kimi-stream-capture-v1'
CAPTURE_SCHEMA = 'session-bench-kimi-survival-capture-v1'
_TURN_DOCUMENTS = ('launch.json', 'exit.json', 'stdout.jsonl', 'stderr.txt', 'workspace-inventory.json', 'workspace/.survival-observer.jsonl',
                   'workspace/checkout.py', 'workspace/bench_check.py')
CONTROLLER_SOURCE = 'controller-source.py'
CAPTURE_DOCUMENTS = ('plan.json', 'capture-result.json', 'workload-instance.json', 'workload-template.json', 'before-workspace.json',
                     CONTROLLER_SOURCE) + tuple(
    f'turn-r{turn}/{name}' for turn in (1, 2) for name in _TURN_DOCUMENTS)
ROOT_LOCATOR = ('KIMI_CODE_HOME/sessions/<workspace-id>/<session-id>/ (KIMI_CODE_HOME is a new directory that holds only config.toml before '
                'the first turn; the session id is the session_id of the resume hint on stdout; the workspace id is wd_<name of the '
                'working directory>_<first 12 hex of the SHA-256 of its path>)')
_PHASES = ('inspect', 'baseline', 'final')
_EMPTY = hashlib.sha256(b'').hexdigest()
_SESSION = re.compile(r'session_[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}')
_ISO = re.compile(r'20[0-9]{2}-[01][0-9]-[0-3][0-9]T[0-2][0-9]:[0-5][0-9]:[0-5][0-9]\.[0-9]{3}Z')
# Files of the isolated home outside the session directory (vendor layout of CLI 2.1.1). Another name is an unknown family.
_HOME_LAYOUT = re.compile(
    r'cache/query-store/(?:cluster\.(?:indexes|meta)\.json|shard-[0-9]{2}/(?:CURRENT|db\.indexes\.json|db\.wal|generations/g-[0-9]{6}/'
    r'(?:compound\.index|dt\.index|manifest\.json|secondary\.index|store)))|config\.toml|device_id|logs/kimi-code\.log|updates/latest\.json|'
    r'updates/rollout\.log|sessions/\.index-cache/scan\.json')


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


def root_row(repetition):
    return {'repetition': repetition, 'root_locator': ROOT_LOCATOR, 'isolated_discovery': True, 'personal_history_scanned': False}


def _argument(argv, flag):
    """The value after ``flag`` in a launch argv; None when the flag is absent or stated twice."""
    places = [index for index, item in enumerate(argv) if item == flag]
    return argv[places[0] + 1] if len(places) == 1 and places[0] + 1 < len(argv) else None


def _independent_capture(documents):
    """Check the independent receipts of one capture. No native file is opened.

    Returns (plan, workload, streams by turn, final helper ledger, checkout before, checkout after, models by turn, session id, build).
    """
    plan, result, workload = (read_json(documents[name], name) for name in ('plan.json', 'capture-result.json', 'workload-instance.json'))
    run_id, scratch = plan.get('attempt_id'), plan.get('scratch_retained')
    _check(plan.get('schema_version') == result.get('schema_version') == CAPTURE_SCHEMA and plan.get('configuration_id') == 'kimi'
           and isinstance(run_id, str) and run_id and result.get('attempt_id') == workload.get('run_id') == run_id
           and result.get('status') == 'captured_pending_qualification' and result.get('cli_invocations') == 2
           and plan.get('max_cli_invocations_per_turn') == 1 and plan.get('no_topup_or_route_switch') is True
           and plan.get('credential_values_retained') is False and result.get('credential_values_retained') is False,
           'Kimi capture identity or completion mismatch')
    # The home, the user home, the skills directory and the workspace are new directories under one scratch directory.
    _check(isinstance(scratch, str) and '/session-bench-kimi-' in scratch and plan.get('kimi_code_home') == scratch + '/kimi'
           and plan.get('isolated_home') == scratch + '/home' and plan.get('skills_dir') == scratch + '/empty-skills'
           and plan.get('workspace') == scratch + '/workspace' and plan.get('fixture') == scratch + '/workspace/fixture_project'
           and result.get('scratch_retained') == scratch, 'Kimi capture does not name one isolated scratch directory')
    _check(workload.get('run_canary') == 'SB_SURVIVAL_V1_RUN_' + run_id, 'Kimi workload canary mismatch')
    _check(sha(documents['workload-template.json']) == plan.get('workload_template_sha256'), 'Kimi workload template digest mismatch')
    # The controller source of the capture is a document of the packet; the plan binds it.
    _check(sha(documents[CONTROLLER_SOURCE]) == plan.get('controller_source_sha256'), 'Kimi controller source differs from the plan')
    alias, provider = plan.get('model_alias'), plan.get('provider')
    _check(isinstance(alias, str) and isinstance(provider, str) and alias.startswith(provider + '/') and len(alias) > len(provider) + 1
           and result.get('route', {}).get('model_alias') == alias and result.get('route', {}).get('provider') == provider,
           'Kimi model route mismatch')
    turns, receipts, protected = workload.get('turns'), result.get('turns'), plan.get('fixture_before')
    _check(isinstance(turns, list) and len(turns) == 2 and isinstance(receipts, list) and len(receipts) == 2
           and isinstance(protected, dict) and {'bench_check.py', 'checkout.py'} <= set(protected), 'Kimi requires two captured turns')
    _check(read_json(documents['before-workspace.json'], 'before workspace') == protected, 'Kimi fixture listing differs from the plan')
    streams, sessions, models, builds = {}, set(), [], set()
    for number, (turn, receipt) in enumerate(zip(turns, receipts), 1):
        prefix = f'turn-r{number}/'
        launch, done = (read_json(documents[prefix + name], name) for name in ('launch.json', 'exit.json'))
        stream, argv = documents[prefix + 'stdout.jsonl'], launch.get('argv')
        _check(turn.get('id') == f'turn-r{number}' and turn.get('revision') == f'r{number}' and isinstance(turn.get('text'), str),
               'Kimi workload turn mismatch')
        _check(isinstance(argv, list) and all(isinstance(item, str) for item in argv) and argv[:1] == [plan.get('kimi_executable')]
               and _argument(argv, '-p') == turn['text'] and _argument(argv, '--output-format') == 'stream-json' and _argument(argv, '-m') == alias
               and _argument(argv, '--skills-dir') == plan['skills_dir'] and launch.get('cwd') == plan['fixture']
               and {'HOME', 'KIMI_CODE_HOME'} <= set(launch.get('env_keys', [])), 'Kimi launch differs from the plan or the workload prompt')
        _check(receipt.get('turn') == number and receipt.get('exit_code') == 0 and receipt.get('response_canary_in_stdout') is True
               and done.get('exit_code') == 0 and done.get('stdout_sha256') == sha(stream)
               and done.get('stderr_sha256') == sha(documents[prefix + 'stderr.txt']) and done.get('session_id') == receipt.get('session_id')
               and done.get('credential_exact_match_in_captured_data') is False, 'Kimi turn receipt or stream digest mismatch')
        try:
            summary = stream_summary(stream, f'stdout R{number}')
        except KimiStreamError as error:
            raise ValueError(f'Kimi stdout stream is not a complete turn: {error}') from error
        canary = turn.get('response_canary')
        _check(summary['session_id'] == done['session_id'] and isinstance(canary, str) and summary['text'].rstrip().endswith(canary)
               and summary['text'].count(canary) == 1, 'Kimi stream identity or canary mismatch')
        # The first launch starts a session; the second resumes it by the id that the first stream printed.
        _check(_argument(argv, '-S') == (None if number == 1 else summary['session_id']) and ('-S' in argv) == (number == 2),
               'Kimi launch does not bind the session')
        _check(sha(documents[prefix + 'workspace/bench_check.py']) == protected['bench_check.py']['sha256'] == workload.get('helper', {}).get('sha256'),
               'Kimi helper changed')
        listed = read_json(documents[prefix + 'workspace-inventory.json'], 'workspace inventory')
        _check(isinstance(listed, dict) and all(isinstance(listed.get(name), dict) and listed[name].get('sha256') == sha(documents[prefix + 'workspace/' + name])
                                                for name in ('checkout.py', 'bench_check.py', '.survival-observer.jsonl')),
               'Kimi workspace copy differs from the workspace listing')
        ledger = documents[prefix + 'workspace/.survival-observer.jsonl']
        rows = [read_json(line, 'helper ledger') for line in ledger.splitlines() if line.strip()]
        _check(len(rows) == (2 if number == 1 else 3), 'Kimi missing or extra helper execution')
        for row, phase in zip(rows, _PHASES):
            nonce = workload['helper']['nonces'].get(phase)
            marker = f'SB_SURVIVAL_V1_HELPER_{phase.upper()}_{nonce} '
            _check(isinstance(nonce, str) and nonce and isinstance(row, dict) and row.get('schema_version') == '1.0-survival-helper-ledger'
                   and row.get('phase') == phase and row.get('helper_nonce') == nonce and row.get('id') == f'helper-{phase}-{nonce}'
                   and row.get('run_canary') == workload['run_canary'] and row.get('argv') == ['python3', 'bench_check.py', phase]
                   and row.get('cwd') == 'fixture_project' and type(row.get('exit_code')) is int
                   and row['exit_code'] == (1 if phase == 'baseline' else 0) and isinstance(row.get('output'), str)
                   and row['output'].startswith(marker), 'Kimi helper identity or outcome mismatch')
        streams[number] = stream
        sessions.add(summary['session_id'])
        builds.add(summary['version'])
        models.append({'model_id': alias[len(provider) + 1:], 'configuration': {'model_alias': alias}})
    _check(len(sessions) == 1 and len(builds) == 1 and result.get('session_id') in sessions and _SESSION.fullmatch(result['session_id']),
           'Kimi turns used different sessions or builds')
    before, after = (documents[f'turn-r{number}/workspace/checkout.py'] for number in (1, 2))
    _check(sha(before) == protected['checkout.py']['sha256'] and before != after, 'Kimi checkout revision boundary mismatch')
    final_ledger = documents['turn-r2/workspace/.survival-observer.jsonl']
    _check(final_ledger.startswith(documents['turn-r1/workspace/.survival-observer.jsonl']), 'Kimi helper ledger was rewritten')
    for line in final_ledger.splitlines():
        row = read_json(line, 'helper ledger')
        _check(row.get('checkout_sha256') == sha(after if row['phase'] == 'final' else before), 'Kimi helper does not bind the captured checkout bytes')
    return plan, workload, streams, final_ledger, before, after, models, result['session_id'], builds.pop()


def observer_from_capture_documents(documents):
    """Build the observer of one capture: tool events from the stdout stream of each turn; no native file is read."""
    _, workload, streams, ledger, before, after, models, _, _ = _independent_capture(documents)
    try:
        observer = build_kimi_stream_observer(workload=workload, stdout_by_turn=streams, helper_document=ledger, before_sha256=sha(before),
                                              after_sha256=sha(after), models=models)
    except KimiStreamError as error:
        raise ValueError(f'Kimi observer cannot be built: {error}') from error
    _validate_observer(observer)
    return observer


def capture_build(documents):
    """The CLI version that both stdout streams state."""
    return _independent_capture(documents)[8]


# --- the isolated home ---------------------------------------------------------------------------
def workspace_id(fixture):
    """The workspace id of a working directory: ``wd_<name>_<first 12 hex of the SHA-256 of the path>``."""
    return f"wd_{fixture.rstrip('/').rsplit('/', 1)[-1]}_{sha(fixture.encode('utf-8'))[:12]}"


def home_classes(inventory, plan, session):
    """Classify every file of one listing of the isolated home; an unknown name raises.

    Returns ``{'session': {relative path: entry}, 'index': [paths], 'empty': [paths], 'other': [paths]}``. ``session`` is the
    session directory. ``index`` is the three files that name the session and can be rebuilt. ``empty`` is every other
    file of size 0. ``other`` is every other file of the vendor layout.
    """
    workspace = workspace_id(plan['fixture'])
    prefix = f'sessions/{workspace}/{session}/'
    classes = {'session': {}, 'index': [], 'empty': [], 'other': []}
    _check(isinstance(inventory, dict), 'Kimi home listing is malformed')
    for path, entry in sorted(inventory.items()):
        _check(isinstance(entry, dict) and set(entry) == {'sha256', 'size_bytes'} and isinstance(entry['sha256'], str)
               and type(entry['size_bytes']) is int and entry['size_bytes'] >= 0, 'Kimi home listing entry is malformed')
        if path.startswith(prefix):
            classes['session'][path[len(prefix):]] = entry
        elif path in ('session_index.jsonl', 'workspaces.json', 'file-history/' + workspace):
            classes['index'].append(path)
        elif re.fullmatch(r'sessions/\.index-dirty/' + re.escape(session) + r'\.[0-9]{13}', path):
            _check(entry['size_bytes'] == 0, 'Kimi index marker is not empty')
            classes['empty'].append(path)
        elif _HOME_LAYOUT.fullmatch(path):
            classes['empty' if entry['size_bytes'] == 0 else 'other'].append(path)
        else:
            raise ValueError('Kimi home holds a file outside the known layout (another session or an unknown family)')
    return classes


def home_index_texts(plan, session, *, touched_at, created_at, last_opened_at):
    """The bytes of the three index files of the home, from values of the capture and three times."""
    workspace, home, fixture = workspace_id(plan['fixture']), plan['kimi_code_home'], plan['fixture']
    compact = lambda value: json.dumps(value, ensure_ascii=False, separators=(',', ':'))  # noqa: E731
    return {
        'session_index.jsonl': compact({'sessionId': session, 'sessionDir': f'{home}/sessions/{workspace}/{session}', 'workDir': fixture}) + '\n',
        'workspaces.json': compact({'version': 1, 'workspaces': {workspace: {'root': fixture, 'name': fixture.rsplit('/', 1)[-1],
                                                                              'created_at': created_at, 'last_opened_at': last_opened_at}},
                                    'deleted_workspace_ids': []}),
        'file-history/' + workspace: compact({'sessions': [{'id': session, 'touchedAt': touched_at}]}),
    }


def _iso(milliseconds):
    return datetime.fromtimestamp(milliseconds // 1000, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.') + f'{milliseconds % 1000:03d}Z'


def home_index_proof(documents, wire):
    """Rebuild the three index files of the home and prove each by the SHA-256 of the home listing after turn 2.

    The files hold the session id, paths of the capture and times. A time is
    not in the capture: it is searched near a time of the wire log (the start
    of the session, the second prompt, the file checkpoint). Raises when a
    file cannot be rebuilt.
    """
    plan, *_, session, _ = _independent_capture(documents)
    listing = read_json(documents['turn-r2/exit.json'], 'exit receipt')['kimi_home_inventory_after']
    rows = [record for _, record in read_wire(wire)]
    created = rows[0]['created_at']
    opened = [record['time'] for record in rows if record.get('type') == 'turn.prompt'][-1]
    touched = [record['time'] for record in rows if str(record.get('type')).startswith('file_history.')]
    workspace = workspace_id(plan['fixture'])
    found = {}
    fixed = home_index_texts(plan, session, touched_at=0, created_at='', last_opened_at='')['session_index.jsonl']
    if sha(fixed.encode()) == listing['session_index.jsonl']['sha256']:
        found['session_index.jsonl'] = fixed
    for base in touched:
        for delta in range(-2000, 2001):
            text = home_index_texts(plan, session, touched_at=base + delta, created_at='', last_opened_at='')['file-history/' + workspace]
            if sha(text.encode()) == listing['file-history/' + workspace]['sha256']:
                found['file-history/' + workspace] = text
    for first in range(created - 400, created + 101):
        if 'workspaces.json' in found:
            break
        for second in range(opened - 600, opened + 101):
            text = home_index_texts(plan, session, touched_at=0, created_at=_iso(first), last_opened_at=_iso(second))['workspaces.json']
            if sha(text.encode()) == listing['workspaces.json']['sha256']:
                found['workspaces.json'] = text
                break
    _check(len(found) == 3, 'a Kimi home index file cannot be rebuilt from the capture')
    proof = {'schema_version': HOME_PROOF_SCHEMA, 'run_id': plan['attempt_id'], 'session_id': session,
             'files': [{'path': path, 'text': text, 'sha256': sha(text.encode()), 'size_bytes': len(text.encode())} for path, text in sorted(found.items())]}
    verify_home_index(proof, plan, session, listing)
    return proof


def verify_home_index(proof, plan, session, listing):
    """Check the rebuilt index files against the home listing and against the values of the capture, or raise."""
    workspace = workspace_id(plan['fixture'])
    files = proof.get('files') if isinstance(proof, dict) else None
    _check(isinstance(proof, dict) and set(proof) == {'schema_version', 'run_id', 'session_id', 'files'} and proof['schema_version'] == HOME_PROOF_SCHEMA
           and proof['run_id'] == plan['attempt_id'] and proof['session_id'] == session and isinstance(files, list)
           and [row.get('path') if isinstance(row, dict) else None for row in files] == sorted(['session_index.jsonl', 'workspaces.json', 'file-history/' + workspace]),
           'Kimi home index proof is not the three index files of this session')
    for row in files:
        text = row.get('text')
        _check(set(row) == {'path', 'text', 'sha256', 'size_bytes'} and isinstance(text, str) and sha(text.encode()) == row['sha256']
               and len(text.encode()) == row['size_bytes'] and listing.get(row['path']) == {'sha256': row['sha256'], 'size_bytes': row['size_bytes']},
               'a rebuilt Kimi home index file differs from the home listing')
        value = read_json(text, row['path'])
        if row['path'] == 'session_index.jsonl':
            expected = home_index_texts(plan, session, touched_at=0, created_at='', last_opened_at='')[row['path']]
            _check(text == expected, 'Kimi session index holds another value')
        elif row['path'] == 'workspaces.json':
            times = value.get('workspaces', {}).get(workspace, {})
            _check(isinstance(times.get('created_at'), str) and isinstance(times.get('last_opened_at'), str) and _ISO.fullmatch(times['created_at'])
                   and _ISO.fullmatch(times['last_opened_at']) and text == home_index_texts(
                       plan, session, touched_at=0, created_at=times['created_at'], last_opened_at=times['last_opened_at'])[row['path']],
                   'Kimi workspace index holds another value')
        else:
            sessions = value.get('sessions')
            _check(isinstance(sessions, list) and len(sessions) == 1 and type(sessions[0].get('touchedAt')) is int
                   and text == home_index_texts(plan, session, touched_at=sessions[0]['touchedAt'], created_at='', last_opened_at='')[row['path']],
                   'Kimi file-history index holds another value')
    return True


def home_files_not_copied(listing, plan, session):
    """The files of the home that the packet does not hold and cannot rebuild, with their sizes (digests are not repeated)."""
    classes = home_classes(listing, plan, session)
    return [{'path': path, 'size_bytes': listing[path]['size_bytes']} for path in classes['other']]


def validate_kimi_capture_assertion(contents, context, native_inventory):
    """Bind the packet to one isolated capture. Returns (complete family, complete root) = (True, True) or raises."""
    assertion = read_json(contents[context['capture_assertion_path']], 'Kimi assertion')
    _check(assertion.get('schema_version') == ASSERTION_SCHEMA and assertion.get('run_id') == context['run_id']
           and assertion.get('repetition') == context['repetition'] and context.get('observer_kind') == OBSERVER_KIND
           and assertion.get('native_inventory_sha256') == sha(contents['native/decode.json']), 'Kimi assertion identity or inventory mismatch')
    for name in ('workload.json', 'observer.json', HOME_PROOF):
        _check(assertion.get('input_sha256', {}).get(name) == sha(contents.get('inputs/' + name, b'')), 'Kimi input binding mismatch')
    documents = {}
    for entry in assertion.get('capture_documents', []):
        name = entry.get('path') if isinstance(entry, dict) else None
        data = contents.get('inputs/capture/' + str(name))
        _check(isinstance(name, str) and '..' not in Path(name).parts and name not in documents and data is not None
               and sha(data) == entry.get('sha256') and len(data) == entry.get('size_bytes'), 'Kimi capture proof member mismatch')
        documents[name] = data
    _check(set(documents) == set(CAPTURE_DOCUMENTS)
           and {name for name in contents if name.startswith('inputs/capture/')} == {'inputs/capture/' + name for name in documents},
           'Kimi capture proof is not the closed document set')
    plan, workload, *_, session, build = _independent_capture(documents)
    _check(plan['attempt_id'] == context['run_id'] and build == context['build'], 'Kimi receipts differ from the packet context')
    launches = [read_json(documents[f'turn-r{number}/launch.json'], 'launch') for number in (1, 2)]
    exits = [read_json(documents[f'turn-r{number}/exit.json'], 'exit') for number in (1, 2)]
    # The home was new: before the first launch it held only the config file that the controller wrote.
    fresh = launches[0].get('kimi_home_inventory_before')
    _check(isinstance(fresh, dict) and set(fresh) == {'config.toml'} and fresh['config.toml'].get('sha256') == plan.get('safe_config_sha256'),
           'Kimi home was not new before the first turn')
    # No writer between the turns: the listing before the second launch equals the listing after the first turn.
    _check(launches[1].get('kimi_home_inventory_before') == exits[0].get('kimi_home_inventory_after'), 'Kimi home changed between the turns')
    listing = exits[1].get('kimi_home_inventory_after')
    classes = home_classes(listing, plan, session)
    home_classes(exits[0].get('kimi_home_inventory_after'), plan, session)
    family = classes['session']
    _check(family and exits[1].get('native_family_source_inventory') == family and exits[1].get('native_family_copied_inventory') == family,
           'Kimi session directory is not quiescent or the copy differs')
    declared = {row.get('path'): row for row in native_inventory['artifacts']}
    prefix = SESSION_DIR + '/'
    _check(set(declared) == {prefix + name for name in family}
           and {name for name in contents if name.startswith('native/') and name != 'native/decode.json'} == {'native/' + name for name in declared},
           'Kimi native inventory is not the copied session directory')
    for name, row in declared.items():
        data, bound = contents['native/' + name], family[name[len(prefix):]]
        _check(sha(data) == row.get('sha256') == bound['sha256'] and len(data) == row.get('size_bytes') == bound['size_bytes'],
               'Kimi native file differs from the exit receipt')
    state = read_json(contents['native/' + prefix + STATE], 'state file')
    wire = contents['native/' + prefix + WIRE]
    _check(isinstance(state, dict) and state.get('id') == session and state.get('cwd') == plan['fixture'], 'Kimi state file names another session')
    proof = read_json(contents['inputs/' + HOME_PROOF], 'home index proof')
    verify_home_index(proof, plan, session, listing)
    _check(assertion.get('home_files_not_copied') == home_files_not_copied(listing, plan, session), 'Kimi list of files outside the packet differs')
    _check(documents['turn-r2/workspace/bench_check.py'] == contents['runtime/fixtures/scenarios/survival-v1/workload/fixture_project/bench_check.py'],
           'Kimi helper differs from the frozen source')
    _check(canonical(observer_from_capture_documents(documents)) == contents['inputs/observer.json']
           and documents['workload-instance.json'] == contents['inputs/workload.json'], 'Kimi observer or workload differs from the capture')
    _check(context.get('required_companions') == sorted(prefix + name for name in family if name != WIRE), 'Kimi companion set differs from the session directory')
    _check(context.get('root_repetitions') == [root_row(context['repetition'])], 'Kimi root row differs from the isolated capture')
    first = read_wire(wire)[0][1]
    _check(type(first.get('created_at')) is int and datetime.fromtimestamp(first['created_at'] / 1000, tz=timezone.utc).date().isoformat() == context['collected_on'],
           'Kimi observation date differs from the native session start')
    return True, True


def check_observed_edit_arguments(observer_document, decoded):
    """Fail unless each observed edit has the same arguments as the native record with its call id.

    The shared comparator relates an observed action to a native one by call id, turn, command, path and kind. It does
    not compare edit text. This check does, for the edit of the workload file: the stream arguments (path, old and new
    text, or the whole new content) must equal the arguments of the native tool call. An observed edit with no
    arguments (no ``input``), with no native record of its call id, or with other arguments, raises. Returns the number
    of native records compared.
    """
    observer = read_json(observer_document, 'Kimi observer')
    native = {}
    for action in decoded['actions']:
        native.setdefault(action.get('call_id'), []).append(action)
    compared = 0
    for event in observer['events']:
        fields = event.get('fields') if event.get('kind') == 'action' else None
        if not isinstance(fields, dict) or fields.get('action_kind') != 'edit':
            continue
        _check(isinstance(fields.get('input'), dict) and fields['input'], 'Kimi observed edit has no arguments')
        _check(native.get(fields.get('call_id')), 'Kimi observed edit has no native record with its call id')
        for action in native[fields['call_id']]:
            _check(action.get('input') == fields['input'], 'Kimi observed edit arguments differ from the native record of the same call')
            compared += 1
    return compared


def build_kimi_replay_evidence(root, native, instance, context, common):
    """Decode the session directory of the packet and build broad evidence for it."""
    root = Path(root)
    decoded = decode_kimi_native(native)
    compared = check_observed_edit_arguments(common['observer_document'], decoded)
    scored = [action for action in instance.get('actions', []) if isinstance(action, dict) and action.get('kind') == 'edit']
    _check(compared or not scored, 'Kimi replay compared no edit although the workload has a scored edit')
    add_native_final_after_chain(decoded, instance)
    common = dict(common)
    names = ['inputs/capture/plan.json', 'inputs/capture/turn-r1/launch.json', 'inputs/capture/turn-r2/exit.json', 'inputs/' + HOME_PROOF]
    common['kimi_root_locators'] = [{'id': name, 'sha256': sha((root / name).read_bytes())} for name in names]
    return decoded, decoded, build_kimi_format_evidence(decoded, context=context, common=common)


def remove_final_response_file(native, output, canary):
    """Selected-loss control: copy the native directory without the final response records. Returns the removed records."""
    native, output = Path(native), Path(output)
    inventory = read_json((native / 'decode.json').read_bytes(), 'Kimi loss inventory')
    removed = []
    for path in sorted(item for item in native.rglob('*') if item.is_file()):
        relative = path.relative_to(native).as_posix()
        data = path.read_bytes()
        if relative == SESSION_DIR + '/' + WIRE:
            data, removed = remove_final_response(data, canary)
            entry = next(row for row in inventory['artifacts'] if row['path'] == relative)
            entry.update(sha256=sha(data), size_bytes=len(data))
        if relative != 'decode.json':
            target = output / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
    (output / 'decode.json').write_bytes(canonical(inventory) + b'\n')
    return removed


__all__ = ['ASSERTION_SCHEMA', 'CAPTURE_DOCUMENTS', 'check_observed_edit_arguments', 'CONTROLLER_SOURCE', 'HOME_PROOF', 'OBSERVER_KIND', 'build_kimi_replay_evidence', 'capture_build',
           'home_classes', 'home_files_not_copied', 'home_index_proof', 'observer_from_capture_documents', 'remove_final_response_file',
           'root_row', 'validate_kimi_capture_assertion', 'verify_home_index', 'workspace_id']
