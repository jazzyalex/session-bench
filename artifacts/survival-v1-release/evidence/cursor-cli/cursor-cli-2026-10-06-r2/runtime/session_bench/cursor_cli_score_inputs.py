"""Source-bound Cursor CLI replay inputs from an isolated-directory capture.

The controller (``cursor_live_capture.py`` at capture time, bound by its
SHA-256) refuses an existing run directory, creates ``cursor-config`` and
``cursor-data`` inside the new run directory and passes them as
``CURSOR_CONFIG_DIR`` and ``CURSOR_DATA_DIR``. ``HOME`` stays the operator's
home. ``plan.json`` is written before the first turn,
``controller-state.json`` after the last. The controller copies the session
family after turn 2 and records its hashes
(``capture/native-manifest.private.json``).

The family is complete: ``store.db``, ``meta.json`` and the agent transcript,
the three files whose path carries the session id. The packet builder lists
both directories (``root-inventory.json``) and the validator requires that
nothing else is in them except the harness files ``cli-config.json`` and
``statsig-cache.json`` (no session id, no run canary, not copied).

The root is not complete. The CLI also writes rows of the session to a shared
store in the operator's home, ``~/.cursor/ai-tracking/ai-code-tracking.db``,
which holds other sessions and cannot be copied whole. So
``portable.complete_root`` is false. The rows of the session were read after
the capture on the owner's decision and are bound in the packet
(``shared-store-extract/ai-code-tracking-rows.json``). The absence rows are
accepted only on the complete family plus these bound rows
(``shared_store_findings``). Nothing outside the two directories was
inventoried at capture time; the adapter document states what is known.
"""
import hashlib
import json
from pathlib import Path
import re

from .cursor_cli_format_evidence import build_cursor_format_evidence
from .cursor_cli_live import (
    add_native_final_after_chain, build_cursor_cli_observer, decode_cursor_cli_native, find_store, scored_pool,
)
from .cursor_cli_store import CursorStoreError, classify_blobs, read_meta, read_store, remove_blobs, first
from .native_replay import canonical

ASSERTION_SCHEMA = 'session-bench-cursor-cli-score-inputs-v1'
OBSERVER_KIND = 'cursor-cli-capture-v1'
CAPTURE_SCHEMA = 'session-bench-cursor-cli-live-capture-v1'
INVENTORY_SCHEMA = 'session-bench-cursor-cli-root-inventory-v1'
ROOT_INDEX_SCHEMA = 'session-bench-cursor-cli-root-repetitions-v1'
ROOT_INDEX_PATH = 'inputs/root-repetitions.json'
CONTROLLER_SHA256 = '79b24a00a036e58b93eff3072f5c7a663fa7120a35d940b6264b5de935b73b26'
ROOT_DOCUMENTS = ('plan.json', 'run-env.json', 'controller-state.json', 'capture/native-manifest.private.json', 'root-inventory.json')
CAPTURE_DOCUMENTS = ROOT_DOCUMENTS + (
    'workload-instance.json', 'controller-source.py',
    'observer/turn-r1.stdout.jsonl', 'observer/turn-r2.stdout.jsonl', 'observer/turn-r1.stderr.txt', 'observer/turn-r2.stderr.txt',
    'observer/helper-ledger-r1.jsonl', 'observer/helper-ledger-r2.jsonl',
    'observer/checkout.before.py', 'observer/checkout.after-r1.py', 'observer/checkout.after-r2.py',
    'project/fixture_project/.survival-observer.jsonl', 'shared-store-extract/ai-code-tracking-rows.json',
)
# The rows of this session in the shared store, read after the capture on the owner's decision.
EXTRACT = 'shared-store-extract/ai-code-tracking-rows.json'
EXTRACT_SCHEMA = 'session-bench-cursor-shared-store-extract-v1'
SHARED_STORE_TABLES = ('ai_code_hashes', 'scored_commits', 'tracking_state', 'conversation_summaries', 'tracked_file_content', 'ai_deleted_files')
# Columns of the two tables that hold rows of a session ("rowid" is the SQLite row id). None is a count.
SHARED_STORE_COLUMNS = {
    'ai_code_hashes': frozenset({'rowid', 'hash', 'source', 'fileExtension', 'fileName', 'requestId', 'conversationId', 'timestamp', 'createdAt', 'model'}),
    'tracked_file_content': frozenset({'rowid', 'gitPath', 'content', 'conversationId', 'model', 'fileExtension', 'createdAt'}),
}
HARNESS_FILES = ('cursor-config/cli-config.json', 'cursor-config/statsig-cache.json')
ROOT_LOCATOR = ('CURSOR_CONFIG_DIR/chats/<MD5 of the workspace path>/<session-id>/ (store.db, meta.json) and '
                'CURSOR_DATA_DIR/projects/<workspace path with "/" as "-">/agent-transcripts/<session-id>/<session-id>.jsonl; '
                'the controller creates both directories for the run and the stream reports the session id; '
                'rows of the session also sit in the shared store ~/.cursor/ai-tracking/ai-code-tracking.db')
_USAGE_KEY = re.compile(r'token|usage|cost|billing|credit', re.I)
_SHA = re.compile(r'[0-9a-f]{64}\Z')
_SHORT_HASH = re.compile(r'[0-9a-f]{1,8}')


def sha(data):
    return hashlib.sha256(data).hexdigest()


def read_json(data, label):
    try:
        return json.loads(data)
    except (ValueError, TypeError) as error:
        raise ValueError(f'{label} is not JSON') from error


def session_paths(workspace, session):
    """The three session files, relative to the run directory, as the CLI derives them from the workspace path."""
    chats = f'cursor-config/chats/{hashlib.md5(workspace.encode()).hexdigest()}/{session}'
    transcript = f"cursor-data/projects/{workspace.replace('/', '-').lstrip('-')}/agent-transcripts/{session}/{session}.jsonl"
    return {'store': chats + '/store.db', 'sidecar': chats + '/meta.json', 'transcript': transcript}


def inventory_isolated_roots(run_root, *, session_id, run_canary):
    """List both isolated directories of a retained run (packet builder). Files are read as bytes only."""
    run_root = Path(run_root)
    entries = []
    for root in ('cursor-config', 'cursor-data'):
        for path in sorted((run_root / root).rglob('*')):
            info = path.lstat()
            if path.is_symlink() or not (path.is_dir() or path.is_file()):
                raise ValueError('isolated root holds a link or a special file')
            row = {'path': path.relative_to(run_root).as_posix(), 'kind': 'directory' if path.is_dir() else 'file',
                   'birthtime_ns': int(info.st_birthtime * 1_000_000_000), 'mtime_ns': info.st_mtime_ns}
            if path.is_file():
                data = path.read_bytes()
                row.update(size_bytes=len(data), sha256=sha(data), holds_session_id=session_id.encode() in data,
                           holds_run_canary=run_canary.encode() in data)
            entries.append(row)
    return {'schema_version': INVENTORY_SCHEMA, 'run_id': run_root.name, 'session_id': session_id,
            'taken': 'by the packet builder from the retained isolated directories', 'entries': entries}


def qualify_isolated_root(documents):
    """Check the receipts of one isolated-root capture, or raise ValueError.

    Returns the run id, session id, build, run directory and the session
    family as {relative path: sha256}.
    """
    value = {name: read_json(documents[name], name) for name in ROOT_DOCUMENTS}
    plan, env, state, manifest, inventory = (value[name] for name in ROOT_DOCUMENTS)
    argv = plan.get('argv_base')
    if (plan.get('schema_version') != CAPTURE_SCHEMA or plan.get('state') != 'prepared' or plan.get('configuration_id') != 'cursor-cli'
            or plan.get('persistence_route') != 'isolated' or plan.get('normal_root') is not None or plan.get('model_submissions') != 0
            or not isinstance(argv, list) or argv.count('--workspace') != 1 or not isinstance(plan.get('attempt_id'), str)):
        raise ValueError('Cursor plan is not a prepared isolated capture')
    workspace = argv[argv.index('--workspace') + 1]
    if not isinstance(workspace, str) or not workspace.endswith('/' + plan['attempt_id'] + '/project'):
        raise ValueError('Cursor workspace is not inside the run directory')
    run_root = workspace[:-len('/project')]
    if (set(env) != {'CURSOR_CONFIG_DIR', 'CURSOR_DATA_DIR', 'SB_SURVIVAL_V1_RUN_CANARY'} or env['CURSOR_CONFIG_DIR'] != run_root + '/cursor-config'
            or env['CURSOR_DATA_DIR'] != run_root + '/cursor-data'):
        raise ValueError('Cursor environment does not bind the isolated directories')
    session, turns = state.get('session_id'), state.get('turns')
    if (state.get('state') != 'captured_private_unqualified' or state.get('reason_ids') != [] or state.get('model_submissions') != 2
            or any(state.get(key) != plan.get(key) for key in ('schema_version', 'attempt_id', 'argv_base', 'build', 'model', 'repetition', 'persistence_route'))
            or not isinstance(session, str) or not session or not isinstance(turns, list) or [row.get('turn') for row in turns] != [1, 2]
            or any(row.get('return_code') != 0 for row in turns)):
        raise ValueError('Cursor controller state is not a completed two-turn capture')
    paths = session_paths(workspace, session)
    copied = {row.get('path'): row.get('sha256') for row in manifest.get('files', [])} if isinstance(manifest.get('files'), list) else {}
    if (manifest.get('schema_version') != CAPTURE_SCHEMA or manifest.get('session_id') != session
            or set(copied) != {'acp/store.db', 'acp/meta.json', 'cursor-session.jsonl'}):
        raise ValueError('Cursor controller manifest does not name the session family')
    entries = inventory.get('entries')
    if (inventory.get('schema_version') != INVENTORY_SCHEMA or inventory.get('run_id') != plan['attempt_id'] or inventory.get('session_id') != session
            or not isinstance(entries, list) or len({row.get('path') for row in entries if isinstance(row, dict)}) != len(entries)):
        raise ValueError('Cursor root inventory is malformed')
    files = {row['path']: row for row in entries if row.get('kind') == 'file'}
    directories = {row['path'] for row in entries if row.get('kind') == 'directory'}
    expected_directories = set()
    for name in paths.values():
        parts = name.split('/')
        expected_directories.update('/'.join(parts[:size]) for size in range(2, len(parts)))
    if set(files) != set(paths.values()) | set(HARNESS_FILES) or directories != expected_directories:
        raise ValueError('Cursor isolated directories hold an entry outside the session family and the harness files')
    started, ended = turns[0].get('started_ns'), turns[1].get('ended_ns')
    for row in entries:
        if (type(row.get('birthtime_ns')) is not int or type(row.get('mtime_ns')) is not int or type(started) is not int or type(ended) is not int
                or row['birthtime_ns'] < started or row['mtime_ns'] > ended):
            raise ValueError('an entry of the isolated directories is older than the first turn or newer than the second')
    for name, row in files.items():
        if type(row.get('size_bytes')) is not int or not isinstance(row.get('sha256'), str) or not _SHA.fullmatch(row['sha256']):
            raise ValueError('Cursor root inventory file row is malformed')
        if name in HARNESS_FILES and (row.get('holds_session_id') is not False or row.get('holds_run_canary') is not False):
            raise ValueError('a harness file holds a session record')
    family = {paths['store']: copied['acp/store.db'], paths['sidecar']: copied['acp/meta.json'], paths['transcript']: copied['cursor-session.jsonl']}
    if any(files[name]['sha256'] != digest for name, digest in family.items()):
        raise ValueError('the session family changed after the controller copied it')
    return {'run_id': plan['attempt_id'], 'session_id': session, 'build': plan.get('build'), 'run_root': run_root, 'family': family,
            'stdout_sha256': [row.get('stdout_sha256') for row in turns]}


def observer_from_capture_documents(documents):
    workload = read_json(documents['workload-instance.json'], 'workload')
    return build_cursor_cli_observer(
        workload=workload, stdout_by_turn={number: documents[f'observer/turn-r{number}.stdout.jsonl'] for number in (1, 2)},
        helper_document=documents['observer/helper-ledger-r2.jsonl'],
        before_sha256=sha(documents['observer/checkout.before.py']), after_sha256=sha(documents['observer/checkout.after-r2.py']))


def expected_root_row(repetition):
    return {'repetition': repetition, 'root_locator': ROOT_LOCATOR, 'isolated_discovery': True, 'personal_history_scanned': False}


def validate_cursor_cli_capture_assertion(contents, context, native_inventory):
    """Bind the packet to one isolated-directory capture. Returns (complete family, complete root) = (True, False) or raises."""
    assertion = read_json(contents[context['capture_assertion_path']], 'Cursor assertion')
    if (assertion.get('schema_version') != ASSERTION_SCHEMA or assertion.get('run_id') != context['run_id'] or assertion.get('repetition') != context['repetition']
            or assertion.get('native_inventory_sha256') != sha(contents['native/decode.json']) or context.get('observer_kind') != OBSERVER_KIND):
        raise ValueError('Cursor assertion identity or inventory mismatch')
    for name in ('workload.json', 'observer.json'):
        if assertion.get('input_sha256', {}).get(name) != sha(contents['inputs/' + name]):
            raise ValueError('Cursor input binding mismatch')
    documents = {}
    for entry in assertion.get('capture_documents', []):
        name = entry.get('path') if isinstance(entry, dict) else None
        data = contents.get('inputs/capture/' + str(name))
        if (not isinstance(name, str) or '..' in Path(name).parts or name in documents or data is None or sha(data) != entry.get('sha256')
                or len(data) != entry.get('size_bytes')):
            raise ValueError('Cursor capture proof member mismatch')
        documents[name] = data
    if set(documents) != set(CAPTURE_DOCUMENTS) or {name for name in contents if name.startswith('inputs/capture/')} != {'inputs/capture/' + name for name in documents}:
        raise ValueError('Cursor capture proof is not the closed document set')
    if sha(documents['controller-source.py']) != CONTROLLER_SHA256:
        raise ValueError('Cursor controller source mismatch')
    fresh = qualify_isolated_root({name: documents[name] for name in ROOT_DOCUMENTS})
    if fresh['run_id'] != context['run_id'] or fresh['build'] != context['build']:
        raise ValueError('Cursor receipts differ from the packet context')
    if fresh['stdout_sha256'] != [sha(documents[f'observer/turn-r{number}.stdout.jsonl']) for number in (1, 2)]:
        raise ValueError('Cursor controller state differs from the retained stdout')
    if documents['observer/helper-ledger-r2.jsonl'] != documents['project/fixture_project/.survival-observer.jsonl']:
        raise ValueError('Cursor helper ledger copy differs from the workspace ledger')
    if canonical(observer_from_capture_documents(documents)) != contents['inputs/observer.json'] or documents['workload-instance.json'] != contents['inputs/workload.json']:
        raise ValueError('Cursor observer or workload differs from the capture')
    declared = {row.get('path'): row.get('sha256') for row in native_inventory['artifacts']}
    if declared != fresh['family'] or any(sha(contents['native/' + name]) != digest for name, digest in declared.items()):
        raise ValueError('Cursor native family differs from the capture receipts')
    store = next(name for name in declared if name.endswith('/store.db'))
    if context.get('required_companions') != sorted(set(declared) - {store}):
        raise ValueError('Cursor companion set differs from the session family')
    # The observation date is the UTC date of the session creation time in the sidecar.
    from datetime import datetime, timezone
    created = read_json(contents['native/' + store[:-len('store.db')] + 'meta.json'], 'Cursor sidecar').get('createdAtMs')
    if type(created) is not int or datetime.fromtimestamp(created / 1000, tz=timezone.utc).date().isoformat() != context['collected_on']:
        raise ValueError('Cursor observation date differs from the native creation time')
    _validate_root_repetitions(contents, context, fresh)
    # The family is complete. The root is not: rows of the session sit in a shared store outside the two directories.
    return True, False


def _validate_root_repetitions(contents, context, current):
    """Bind the three stable-root rows to three qualified isolated-root captures."""
    rows = context.get('root_repetitions')
    prefix = 'inputs/root-repetitions/'
    extra = {name for name in contents if name.startswith(prefix)}
    if rows is None:
        if ROOT_INDEX_PATH in contents or extra:
            raise ValueError('Cursor root repetition evidence without a context declaration')
        return
    if rows != [expected_root_row(number) for number in (1, 2, 3)] or ROOT_INDEX_PATH not in contents:
        raise ValueError('Cursor stable-root claim requires three indexed isolated-root captures')
    index = read_json(contents[ROOT_INDEX_PATH], 'Cursor root repetitions')
    runs = index.get('runs') if isinstance(index, dict) else None
    if (not isinstance(index, dict) or set(index) != {'schema_version', 'runs'} or index['schema_version'] != ROOT_INDEX_SCHEMA
            or not isinstance(runs, list) or [row.get('repetition') if isinstance(row, dict) else None for row in runs] != [1, 2, 3]):
        raise ValueError('unsupported Cursor root repetition evidence')
    seen, used = [], set()
    for row in runs:
        if set(row) != {'repetition', 'run_id', 'session_id', 'documents'} or not isinstance(row['documents'], list):
            raise ValueError('malformed Cursor root repetition row')
        number = row['repetition']
        base = 'inputs/capture/' if number == context['repetition'] else f'{prefix}repetition-{number}/'
        documents = {}
        for entry in row['documents']:
            if not isinstance(entry, dict) or set(entry) != {'path', 'sha256', 'size_bytes'} or entry['path'] not in ROOT_DOCUMENTS or entry['path'] in documents:
                raise ValueError('malformed Cursor root repetition inventory')
            data = contents.get(base + entry['path'])
            if data is None or sha(data) != entry['sha256'] or len(data) != entry['size_bytes']:
                raise ValueError('Cursor root repetition document mismatch')
            documents[entry['path']] = data
            if number != context['repetition']:
                used.add(base + entry['path'])
        if set(documents) != set(ROOT_DOCUMENTS):
            raise ValueError('Cursor root repetition receipts incomplete')
        fresh = current if number == context['repetition'] else qualify_isolated_root(documents)
        if fresh['run_id'] != row['run_id'] or fresh['session_id'] != row['session_id'] or fresh['build'] != current['build']:
            raise ValueError('Cursor root repetition identity or build mismatch')
        if read_json(documents['plan.json'], 'plan').get('repetition') != number:
            raise ValueError('Cursor root repetition number differs from its plan')
        seen.append((fresh['run_id'], fresh['session_id'], fresh['run_root']))
    if any(len({item[position] for item in seen}) != 3 for position in range(3)):
        raise ValueError('Cursor root repetitions reuse a run, session or root')
    if used != extra:
        raise ValueError('Cursor cross-repetition files differ from their inventory')


def _keys(value, found):
    if isinstance(value, dict):
        for key, item in value.items():
            found.add(key)
            _keys(item, found)
    elif isinstance(value, list):
        for item in value:
            _keys(item, found)
    return found


def usage_key_findings(native):
    """Key names of the session family that could hold a token count (absence scan).

    The scan covers every file of the family: the ``meta`` row, every JSON
    message blob, the pending message of a root, the sidecar and every line of
    the agent transcript. The transcript is opened here, so it is a container
    of the read and its records count for duplicate safety and density. The
    protobuf fields are covered by the decoder contract: every field is in the
    field map and the only token numbers there are the context-window
    estimates of root field 5.
    """
    native = Path(native)
    directory, session = find_store(native)
    found = set()
    store = read_store((native / directory / 'store.db').read_bytes())
    _keys(read_meta(store), found)
    blobs, _ = classify_blobs(store)
    for entry in blobs.values():
        if entry['class'] == 'message':
            _keys(entry['value'], found)
        elif entry['class'] == 'root' and isinstance(first(entry['value'], 4), str):
            try:
                _keys(json.loads(first(entry['value'], 4)), found)
            except ValueError:
                found.add('unreadable pending message (token)')
    _keys(json.loads((native / directory / 'meta.json').read_bytes()), found)
    transcripts = sorted(native.glob(f'cursor-data/projects/*/agent-transcripts/{session}/{session}.jsonl'))
    if len(transcripts) != 1:
        return ['agent transcript missing (token)']
    for line in transcripts[0].read_bytes().splitlines():
        if line.strip():
            _keys(json.loads(line), found)
    return sorted(key for key in found if _USAGE_KEY.search(key))


def shared_store_findings(extract, decoded, *, public=False):
    """What the bound shared-store rows of the session prove.

    ``extract`` is the bytes of the extract document, ``decoded`` the decoded
    chat store of the same packet. Returns ``{'proved': bool, 'reasons': [...]}``.
    The absence is proved only when the extract names this session and the six
    known tables, every row has exactly the known columns of its table, no
    column name or text value names a token count, and the rows describe the
    captured session: the conversation id, the call id and the path of the
    native edit, a time inside the native edit step, and file content whose
    SHA-256 is the native post-edit hash. An unknown table or column could be
    a count; then nothing is proved.

    ``hash`` of ``ai_code_hashes`` is a vendor digest of up to eight hex
    characters (32 bits, no leading zero) with an unknown preimage, and
    ``rowid`` is the row number in the operator's store. A private packet must
    hold the real values. A public packet (``public=True``) must hold them
    zeroed: only zeros at equal length, and row id 0.
    No check reads their value.
    """
    reasons = []
    try:
        document = json.loads(extract)
    except (ValueError, TypeError):
        return {'proved': False, 'reasons': ['extract is not JSON']}
    session, rows = decoded.get('session_id'), document.get('rows') if isinstance(document, dict) else None
    if (not isinstance(document, dict) or document.get('schema_version') != EXTRACT_SCHEMA or document.get('session_id') != session
            or not isinstance(rows, dict)):
        return {'proved': False, 'reasons': ['extract does not name this session']}
    if document.get('tables_in_store') != list(SHARED_STORE_TABLES):
        reasons.append('unknown table set of the shared store')
    if set(rows) - set(SHARED_STORE_COLUMNS) or any(not isinstance(items, list) for items in rows.values()):
        reasons.append('rows of a table outside the known two')
    changes = [row for row in decoded.get('file_changes', []) if isinstance(row.get('after_sha256'), str)]
    edits = {row['id']: row for row in decoded.get('actions', []) if row.get('action_kind') == 'edit'}
    results = {row['action_id']: row for row in decoded.get('results', [])}
    if decoded.get('status') != 'ok' or len(changes) != 1 or changes[0]['action_id'] not in edits:
        return {'proved': False, 'reasons': reasons + ['the captured store holds no single native edit to compare']}
    change = changes[0]
    edit, done = edits[change['action_id']], results.get(change['action_id'], {})
    inside = lambda value: type(value) is int and type(edit.get('timestamp')) is int and type(done.get('timestamp')) is int and edit['timestamp'] <= value <= done['timestamp']
    path = edit['input'].get('path') if isinstance(edit.get('input'), dict) else None
    for table, items in rows.items():
        known = SHARED_STORE_COLUMNS.get(table)
        for row in items if isinstance(items, list) and known else []:
            if not isinstance(row, dict) or set(row) != known:
                reasons.append(f'{table}: unknown or missing column')
                continue
            if any(_USAGE_KEY.search(key) for key in row):
                reasons.append(f'{table}: a column names a count')
            if type(row['rowid']) is not int or (row['rowid'] != 0 if public else row['rowid'] < 1):
                reasons.append(f'{table}: row id is not in the ' + ('zeroed public' if public else 'private') + ' form')
            if table == 'ai_code_hashes' and (not isinstance(row['hash'], str) or not _SHORT_HASH.fullmatch(row['hash'])
                                              or (set(row['hash']) == {'0'}) != public):
                reasons.append('ai_code_hashes: hash is not in the ' + ('zeroed public' if public else 'private') + ' form')
            if row['conversationId'] != session or not inside(row['createdAt']):
                reasons.append(f'{table}: row is not of this session or not inside the native edit step')
            if table == 'ai_code_hashes' and (row['requestId'] != edit['call_id'] or row['fileName'] != path or not inside(row['timestamp'])):
                reasons.append('ai_code_hashes: row does not name the native edit call and its file')
            if table == 'tracked_file_content' and (not isinstance(row['content'], str) or sha(row['content'].encode('utf-8')) != change['after_sha256']
                                                    or not isinstance(path, str) or not isinstance(row['gitPath'], str) or not path.endswith('/' + row['gitPath'])):
                reasons.append('tracked_file_content: row is not the post-edit file of the native edit')
    if not rows.get('ai_code_hashes') or len(rows.get('tracked_file_content', [])) != 1:
        reasons.append('the rows of the edit are missing')
    return {'proved': not reasons, 'reasons': sorted(set(reasons))}


def apply_cursor_cli_native_absence(measurement, decoded, findings, shared):
    """Resolve usage, token semantics and reconciliation as native_absent from evidence in hand.

    The root is not complete, so the rubric path is the complete id-named
    family plus the bound rows of every shared store that names the session.
    The proof needs a clean decode under the contract, an absence scan of the
    family without a finding, and shared-store rows that prove the absence
    (``shared_store_findings``). Anything less stays unresolved.
    """
    rows = [dict(row) for row in measurement['metrics']]
    proved = (decoded.get('status') == 'ok' and not decoded.get('diagnostics') and findings == [] and not decoded.get('usage')
              and isinstance(shared, dict) and shared.get('proved') is True)
    if proved:
        for row in rows:
            if (row['id'] in {'attribution.usage', 'attribution.token_semantics', 'attribution.reconciliation'}
                    and row['state'] == 'unresolved' and row['correct'] == 0 and row['decoded_eligible'] == 0):
                row['state'] = 'native_absent'
    return {**measurement, 'metrics': rows}


def packet_native_absence(measurement, decoded, root, native):
    """Replay entry point: scan the copied family and read the bound extract of this packet."""
    path = Path(root) / 'inputs/capture' / EXTRACT
    try:
        findings = usage_key_findings(native)
        # A sanitized packet carries its transformation receipt; its extract holds the zeroed public form.
        public = (Path(root) / 'inputs/public-transformation.json').is_file()
        shared = shared_store_findings(path.read_bytes(), decoded, public=public) if path.is_file() else {'proved': False}
    except (ValueError, OSError, CursorStoreError):
        return measurement
    return apply_cursor_cli_native_absence(measurement, decoded, findings, shared)


def build_cursor_cli_replay_evidence(root, native, instance, context, common):
    """Decode the store of the packet and build broad evidence for the session family."""
    decoded = decode_cursor_cli_native(native)
    add_native_final_after_chain(decoded, instance)
    observer = read_json((Path(root) / 'inputs/observer.json').read_bytes(), 'observer')
    projected = scored_pool(decoded, observer)
    common = dict(common)
    if context.get('root_repetitions') is not None:
        names = [ROOT_INDEX_PATH] + ['inputs/capture/' + name for name in ROOT_DOCUMENTS]
        common['cursor_root_locators'] = [{'id': name, 'sha256': sha((Path(root) / name).read_bytes())} for name in names]
    return decoded, projected, build_cursor_format_evidence(decoded, projected, context=context, common=common)


def remove_final_response(native, output, canary):
    """Selected-loss control: copy the family without the two blobs that state the response with ``canary``.

    Returns the removed records. The text step and the JSON assistant message
    are removed; the references to them stay and dangle.
    """
    native, output = Path(native), Path(output)
    directory, _ = find_store(native)
    removed = []
    for path in sorted(item for item in native.rglob('*') if item.is_file()):
        relative = path.relative_to(native).as_posix()
        data = path.read_bytes()
        if relative == directory + '/store.db':
            store = read_store(data)
            blobs, _ = classify_blobs(store)
            targets = []
            for identity, entry in blobs.items():
                if entry['class'] == 'step' and (first(entry['value'], 1, 1) or '').rstrip().endswith(canary):
                    targets.append(identity)
                elif entry['class'] == 'message' and entry['value'].get('role') == 'assistant' and any(
                        isinstance(part, dict) and part.get('type') == 'text' and str(part.get('text')).rstrip().endswith(canary)
                        for part in entry['value'].get('content') if isinstance(entry['value'].get('content'), list)):
                    targets.append(identity)
            if len(targets) != 2:
                raise ValueError('Cursor loss control requires one final response in both statements')
            removed = [{'path': relative, 'table': 'blobs', 'rowid': blobs[identity]['rowid'], 'sha256': identity} for identity in targets]
            data = remove_blobs(data, targets)
        target = output / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    return removed
