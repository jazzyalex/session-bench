"""Source-bound Copilot replay inputs; unproved family/root stays unresolved.

A fresh-root capture (controller ``SOURCE_FRESH_ROOT``) carries root-start,
root-end, launch and exit receipts. They prove an empty isolated HOME and
COPILOT_HOME before the first turn, one session directory in that root, no
change of that directory from the end of R2 through the copy, and equal hashes
of the source and the copied files. Only then are the family and the root
complete. The declared root is the session directory plus the session store of
COPILOT_HOME (``session-store.db`` and ``session-store.db-wal``): the store holds
the per-call usage records of the session. The two store files must equal the
hashes of the root-end receipt and must not change from the end of R2 through
the copy. The ``-shm`` file is a rebuildable index and is not part of the root.
"""
import json
import re
from pathlib import Path
from .native_replay import canonical
from .copilot_live import decode_copilot_native, build_copilot_observer, sha
from .copilot_capture_qualification import _json_bytes, _manifest_hash
from .copilot_format_evidence import build_copilot_format_evidence
from .copilot_session_store import STORE_FILES, SessionStoreError, attach_store_usage, read_session_store_files

ASSERTION_SCHEMA = 'session-bench-copilot-score-inputs-v1'
SOURCE06 = '1a5d684c36fbc69ca069047aa32e021a38d47a02a6ad16ecd72593e0b40baf4d'
SOURCE_FRESH_ROOT = 'f6a4d419022466b897ff53b8e354acbb87301d5585492a9f3f297d9d7b44c1ab'
ROOT_SCHEMA = 'session-bench-copilot-fresh-root-v1'
ROOT_INDEX_SCHEMA = 'session-bench-copilot-root-repetitions-v1'
ROOT_INDEX_PATH = 'inputs/root-repetitions.json'
ROOT_DOCUMENTS = ('attempt.json', 'root-start.json', 'root-end.json', 'capture/r1.launch.json', 'capture/r2.launch.json',
                  'capture/r1.exit.json', 'capture/r2.exit.json')
ROOT_LOCATOR = 'COPILOT_HOME/session-state/<session-id>/ plus COPILOT_HOME/session-store.db and session-store.db-wal (session id given as --session-id; COPILOT_HOME is a new empty directory)'
STORE_RECEIPT = 'native-store/retrieval-receipt.json'
STORE_RECEIPT_SCHEMA = 'session-bench-copilot-session-store-retrieval-v1'
FAMILY06 = {'events.jsonl','workspace.yaml','.workspace-fork.lock','checkpoints/index.md','rewind-file-snapshots/index.json','rewind-file-snapshots/tracking.json','rewind-file-snapshots/backups/f4482bd25089ad5c521d2d8c75d17607d5dec0582b9bb687b5716c9673546401'}


def observer_from_capture_documents(documents):
    workload = _json_bytes(documents['workload_instance.json'], 'workload')
    before = _json_bytes(documents['filesystem/initial-state.json'], 'filesystem before')
    after = _json_bytes(documents['filesystem/after-state.json'], 'filesystem after')
    return build_copilot_observer(workload=workload, stdout_by_turn={n:documents[f'capture/r{n}.stdout'] for n in (1,2)},
      helper_document=documents['workspace/fixture_project/.survival-observer.jsonl'],
      before_sha256=_manifest_hash(before['files']['checkout.py']), after_sha256=_manifest_hash(after['files']['checkout.py']))


def validate_copilot_capture_assertion(contents, context, native_inventory):
    assertion = _json_bytes(contents[context['capture_assertion_path']], 'Copilot assertion')
    if assertion.get('schema_version') != ASSERTION_SCHEMA or assertion.get('run_id') != context['run_id'] or assertion.get('repetition') != context['repetition'] or assertion.get('native_inventory_sha256') != sha(contents['native/decode.json']):
        raise ValueError('Copilot assertion identity/inventory mismatch')
    for name in ('workload.json','observer.json'):
        if assertion.get('input_sha256',{}).get(name) != sha(contents['inputs/'+name]): raise ValueError('Copilot input binding mismatch')
    docs = {}
    for entry in assertion.get('capture_documents',[]):
        name = entry.get('path'); data = contents.get('inputs/capture/'+str(name))
        if not isinstance(name,str) or '..' in Path(name).parts or name in docs or data is None or sha(data)!=entry.get('sha256') or len(data)!=entry.get('size_bytes'):
            raise ValueError('Copilot capture proof member mismatch')
        docs[name]=data
    required = {'attempt.json','qualification.json','workload_instance.json','workload_template.json','capture/r1.stdout','capture/r2.stdout',
      'capture/r1.stderr','capture/r2.stderr','filesystem/initial-state.json','filesystem/after-state.json','workspace/fixture_project/.survival-observer.jsonl'}
    if not required <= docs.keys(): raise ValueError('Copilot capture proof incomplete')
    attempt = _json_bytes(docs['attempt.json'],'attempt'); qualification = _json_bytes(docs['qualification.json'],'qualification')
    if attempt.get('status')!='completed' or attempt.get('exit_code')!=0 or attempt.get('session_id')!=qualification.get('session_id') or qualification.get('run_id')!=context['run_id']:
        raise ValueError('Copilot capture completion mismatch')
    if canonical(observer_from_capture_documents(docs)) != contents['inputs/observer.json'] or docs['workload_instance.json'] != contents['inputs/workload.json']:
        raise ValueError('Copilot independent observer/workload differs from capture')
    session = attempt['session_id']; declared = {a['path'] for a in native_inventory['artifacts']}
    for artifact in native_inventory['artifacts']:
        name = artifact['path']; data = contents['native/'+name]
        source = 'native-store/'+name if name in STORE_FILES else 'native/'+session+'/'+name
        if sha(data)!=artifact['sha256'] or docs.get(source)!=data: raise ValueError('Copilot native family differs from capture')
    family = False
    if attempt.get('controller_sha256') == SOURCE_FRESH_ROOT:
        if sha(docs.get('controller-source.py', b'')) != SOURCE_FRESH_ROOT:
            raise ValueError('Copilot fresh-root controller source mismatch')
        if not set(ROOT_DOCUMENTS) <= docs.keys(): raise ValueError('Copilot fresh-root receipts incomplete')
        fresh = qualify_fresh_root({name: docs[name] for name in ROOT_DOCUMENTS})
        if (fresh['session_id'] != session or fresh['run_id'] != context['run_id'] or fresh['cli_version'] != context['build']
                or {**fresh['family'], **fresh['store']} != {a['path']: a['sha256'] for a in native_inventory['artifacts']}):
            raise ValueError('Copilot fresh-root receipts differ from the scored native family')
        if set(attempt.get('native_files', [])) != {'native/' + session + '/' + name for name in declared - set(STORE_FILES)}:
            raise ValueError('Copilot retained family manifest mismatch')
        # The store was copied from the retained root after the capture.
        retrieval = _json_bytes(docs.get(STORE_RECEIPT, b'null'), 'Copilot session store retrieval receipt')
        if (not isinstance(retrieval, dict) or retrieval.get('schema_version') != STORE_RECEIPT_SCHEMA or retrieval.get('run_id') != context['run_id']
                or {name: row.get('sha256') for name, row in retrieval.get('files', {}).items()} != fresh['store']
                or any(row.get('size_bytes') != len(contents['native/' + name]) for name, row in retrieval['files'].items())):
            raise ValueError('Copilot session store differs from its retrieval receipt')
        for number in (1, 2):
            if _json_bytes(docs[f'capture/r{number}.exit.json'], 'exit receipt').get('stdout_sha256') != sha(docs[f'capture/r{number}.stdout']):
                raise ValueError('Copilot exit receipt differs from the retained stdout')
        if context.get('required_companions') != sorted(declared - {'events.jsonl'}):
            raise ValueError('Copilot companion set differs from the copied directory')
        # The observation date is the UTC date of the native session start.
        started = _json_bytes(contents['native/events.jsonl'].split(b'\n', 1)[0], 'Copilot session start')
        if started.get('type') != 'session.start' or str(started.get('data', {}).get('startTime'))[:10] != context['collected_on']:
            raise ValueError('Copilot observation date differs from the native session start')
        _validate_root_repetitions(contents, context, fresh)
        return True, True
    if attempt.get('controller_sha256') == SOURCE06:
        # The exact controller source is retained, but its preserve() routine
        # verifies only copied fixture files. It does not verify native-copy
        # equality or quiescence, so the native family remains unproved.
        if sha(docs.get('controller-source.py', b'')) != SOURCE06 or declared != FAMILY06:
            raise ValueError('Copilot declared native family/source mismatch')
        if set(attempt.get('native_files',[])) != {'native/'+session+'/'+name for name in declared}:
            raise ValueError('Copilot retained family manifest mismatch')
    if context.get('complete_record_family') is True and not family: raise ValueError('Copilot incomplete family cannot be upgraded')
    if context.get('complete_root') is True and not family: raise ValueError('Copilot incomplete root cannot be upgraded')
    if context.get('root_repetitions') not in (None,[]): raise ValueError('Copilot three-root stability proof unavailable')
    return False, False


def _session_slice(inventory, session):
    """The hashes of one session directory inside a COPILOT_HOME inventory."""
    prefix = 'session-state/' + session + '/'
    return {name[len(prefix):]: row.get('sha256') for name, row in inventory.items() if name.startswith(prefix)}


def qualify_fresh_root(documents):
    """Check the receipts of one fresh-root capture.

    Returns the run id, session id, CLI build and the copied family as
    {relative path: sha256}. Raises ValueError when the receipts do not prove
    an isolated root, a single session, quiescence and an equal copy.
    """
    value = {name: _json_bytes(documents[name], name) for name in ROOT_DOCUMENTS}
    attempt, start, end = value['attempt.json'], value['root-start.json'], value['root-end.json']
    session = attempt.get('session_id')
    if (attempt.get('status') != 'completed' or attempt.get('controller_sha256') != SOURCE_FRESH_ROOT
            or not isinstance(session, str) or not session or not isinstance(attempt.get('run_id'), str)):
        raise ValueError('Copilot fresh-root attempt is not a completed controlled capture')
    temporary = attempt.get('temporary_root_retained')
    if (start.get('schema_version') != ROOT_SCHEMA or end.get('schema_version') != ROOT_SCHEMA
            or start.get('controller_sha256') != SOURCE_FRESH_ROOT or not isinstance(temporary, str) or not temporary
            or start.get('HOME') != temporary + '/home' or start.get('COPILOT_HOME') != temporary + '/copilot'
            or start.get('workspace') != temporary + '/fixture_project'
            or any(end.get(key) != start.get(key) for key in ('HOME', 'COPILOT_HOME', 'workspace'))):
        raise ValueError('Copilot fresh-root receipts do not name one isolated root')
    if start.get('home_inventory_before') != {} or start.get('copilot_home_inventory_before') != {}:
        raise ValueError('Copilot root was not empty before the first turn')
    launches = [value[f'capture/r{number}.launch.json'] for number in (1, 2)]
    exits = [value[f'capture/r{number}.exit.json'] for number in (1, 2)]
    for number, launch in enumerate(launches, 1):
        environment, argv = launch.get('safe_environment'), launch.get('argv')
        flag = '--session-id' if number == 1 else '--resume'
        if (not isinstance(environment, dict) or environment.get('HOME') != start['HOME']
                or environment.get('COPILOT_HOME') != start['COPILOT_HOME'] or launch.get('cwd') != start['workspace']
                or not isinstance(argv, list) or argv.count(flag) != 1 or argv[-2:] != [flag, session]
                or '--no-custom-instructions' not in argv):
            raise ValueError('Copilot launch receipt does not bind the isolated root and session')
    if launches[0].get('copilot_home_inventory_before') != {} or launches[0].get('isolated_home_inventory_before') != {}:
        raise ValueError('Copilot root was not empty at the first launch')
    if any(row.get('returncode') != 0 for row in exits):
        raise ValueError('Copilot turn did not exit cleanly')
    inventories = [launches[1].get('copilot_home_inventory_before'), exits[0].get('copilot_home_inventory_after'),
                   exits[1].get('copilot_home_inventory_after'), end.get('copilot_home_inventory_after')]
    if any(not isinstance(item, dict) or not all(isinstance(row, dict) for row in item.values()) for item in inventories):
        raise ValueError('Copilot root inventory is malformed')
    for inventory in inventories:
        owners = {name.split('/')[1] for name in inventory if name.startswith('session-state/')}
        if owners - {session, '.session-operation-locks'} or session not in owners:
            raise ValueError('Copilot root holds another session directory')
    # No writer between the turns, and none from the end of R2 through the copy.
    if _session_slice(inventories[0], session) != _session_slice(inventories[1], session):
        raise ValueError('Copilot session directory changed between the turns')
    family = _session_slice(inventories[3], session)
    selected, copied = end.get('selected_session_inventory'), end.get('copied_session_inventory')
    if (end.get('selected_session_id') != session or not family or not isinstance(selected, dict) or not isinstance(copied, dict)
            or _session_slice(inventories[2], session) != family
            or {name: row.get('sha256') for name, row in selected.items()} != family
            or {name: row.get('sha256') for name, row in copied.items()} != family):
        raise ValueError('Copilot session directory is not quiescent or the copy differs')
    # The session store: both files unchanged from the end of R2 through the copy.
    store = {name: inventories[3].get(name, {}).get('sha256') for name in STORE_FILES}
    if any(not isinstance(value, str) for value in store.values()) or store != {name: inventories[2].get(name, {}).get('sha256') for name in STORE_FILES}:
        raise ValueError('Copilot session store is missing from the receipts or is not quiescent')
    return {'run_id': attempt['run_id'], 'session_id': session, 'cli_version': attempt.get('cli_version'),
            'temporary_root': temporary, 'family': family, 'store': store}


def expected_root_row(repetition):
    return {'repetition': repetition, 'root_locator': ROOT_LOCATOR, 'isolated_discovery': True, 'personal_history_scanned': False}


def _validate_root_repetitions(contents, context, current):
    """Bind the three stable-root rows to three qualified fresh-root captures."""
    rows = context.get('root_repetitions')
    prefix = 'inputs/root-repetitions/'
    extra = {name for name in contents if name.startswith(prefix)}
    if rows is None:
        if ROOT_INDEX_PATH in contents or extra: raise ValueError('Copilot root repetition evidence without a context declaration')
        return
    if rows != [expected_root_row(number) for number in (1, 2, 3)] or ROOT_INDEX_PATH not in contents:
        raise ValueError('Copilot stable-root claim requires three indexed fresh-root captures')
    index = _json_bytes(contents[ROOT_INDEX_PATH], 'Copilot root repetitions')
    runs = index.get('runs') if isinstance(index, dict) else None
    if (not isinstance(index, dict) or set(index) != {'schema_version', 'runs'} or index['schema_version'] != ROOT_INDEX_SCHEMA
            or not isinstance(runs, list) or [row.get('repetition') if isinstance(row, dict) else None for row in runs] != [1, 2, 3]):
        raise ValueError('unsupported Copilot root repetition evidence')
    seen, used = [], set()
    for row in runs:
        if set(row) != {'repetition', 'run_id', 'session_id', 'documents'} or not isinstance(row['documents'], list):
            raise ValueError('malformed Copilot root repetition row')
        number = row['repetition']
        base = 'inputs/capture/' if number == context['repetition'] else f'{prefix}repetition-{number}/'
        documents = {}
        for entry in row['documents']:
            if not isinstance(entry, dict) or set(entry) != {'path', 'sha256', 'size_bytes'} or entry['path'] not in ROOT_DOCUMENTS or entry['path'] in documents:
                raise ValueError('malformed Copilot root repetition inventory')
            data = contents.get(base + entry['path'])
            if data is None or sha(data) != entry['sha256'] or len(data) != entry['size_bytes']:
                raise ValueError('Copilot root repetition document mismatch')
            documents[entry['path']] = data
            if number != context['repetition']: used.add(base + entry['path'])
        if set(documents) != set(ROOT_DOCUMENTS): raise ValueError('Copilot root repetition receipts incomplete')
        fresh = current if number == context['repetition'] else qualify_fresh_root(documents)
        if number == context['repetition'] and documents != {name: contents['inputs/capture/' + name] for name in ROOT_DOCUMENTS}:
            raise ValueError('Copilot root repetition differs from this capture')
        if (fresh['run_id'] != row['run_id'] or fresh['session_id'] != row['session_id'] or fresh['cli_version'] != current['cli_version']):
            raise ValueError('Copilot root repetition identity or build mismatch')
        seen.append((fresh['run_id'], fresh['session_id'], fresh['temporary_root']))
    if any(len({item[position] for item in seen}) != 3 for position in range(3)):
        raise ValueError('Copilot root repetitions reuse a run, session or root')
    if used != extra: raise ValueError('Copilot cross-repetition files differ from their inventory')


def build_copilot_replay_evidence(root, native, instance, context, common):
    decoded=decode_copilot_native(Path(native)/'events.jsonl')
    _add_native_final_after_chain(decoded, instance)
    _add_native_snapshot_change(decoded, Path(native))
    if (Path(native) / STORE_FILES[0]).is_file():
        try:
            attach_store_usage(decoded, read_session_store_files(native))
        except SessionStoreError as error:
            decoded['session_store'] = {'read': False, 'exceptions': [{'code': 'session_store_unreadable', 'detail': str(error)}]}
    common = dict(common)
    if context.get('root_repetitions') is not None:
        # Locators of the receipts that carry the three root rows.
        names = [ROOT_INDEX_PATH] + ['inputs/capture/' + name for name in ('root-start.json', 'root-end.json', STORE_RECEIPT)]
        common['copilot_root_locators'] = [{'id': name, 'sha256': sha((Path(root) / name).read_bytes())} for name in names]
    format_document=build_copilot_format_evidence(decoded,context=context,common=common)
    return decoded,decoded,format_document


def apply_copilot_native_absence(measurement, decoded, *, complete_root):
    """Resolve response usage as native_absent only when the complete root proves it.

    The complete root includes the session store, the one place where the
    harness keeps per-call usage. The absence is proved only when that store
    was read under its contract and holds no usage row at all. A store with
    rows that do not join stays unresolved, and so does every unproved root.
    """
    rows = [dict(row) for row in measurement['metrics']]
    store = decoded.get('session_store')
    proved = (complete_root is True and decoded.get('status') == 'ok' and not decoded.get('diagnostics')
              and isinstance(store, dict) and store.get('read') is True and not store.get('exceptions')
              and store.get('usage_row_count') == 0)
    if proved:
        for row in rows:
            if row['id'] in {'attribution.usage', 'attribution.token_semantics'} and row['state'] == 'unresolved' and row['correct'] == 0:
                row['state'] = 'native_absent'
    return {**measurement, 'metrics': rows}


def apply_copilot_patch(before, patch):
    """Apply one native ``apply_patch`` update to the pre-image text; None when it does not apply exactly once."""
    lines = patch.split('\n')
    if lines and lines[-1] == '': lines.pop()
    if len(lines) < 4 or lines[0] != '*** Begin Patch' or lines[-1] != '*** End Patch' or not lines[1].startswith('*** Update File: '):
        return None
    hunks, current = [], None
    for line in lines[2:-1]:
        if line.startswith('@@'):
            if line.strip() != '@@': return None
            current = []; hunks.append(current)
        elif current is None or line[:1] not in (' ', '-', '+'):
            return None
        else:
            current.append(line)
    if not hunks: return None
    text = before
    for hunk in hunks:
        old = ''.join(line[1:] + '\n' for line in hunk if line[0] in ' -')
        new = ''.join(line[1:] + '\n' for line in hunk if line[0] in ' +')
        if not old or text.count(old) != 1: return None
        text = text.replace(old, new)
    return text


def _add_native_final_after_chain(decoded, instance):
    """Project only a contiguous explicit native R2/edit/test/response chain.

    File hashes and observer relations are deliberately not inputs. Physical
    parent IDs, successful native call completions and the final native
    response must all survive; chronological order alone is insufficient.
    """
    if decoded['status'] != 'ok' or len(decoded['turns']) != 2:
        return
    r2 = decoded['turns'][1]
    responses = [r for r in decoded['responses'] if r.get('turn_id') == r2['id']
                 and r.get('canary') == instance['turns'][1]['response_canary']]
    edits = [a for a in decoded['actions'] if a.get('turn_id') == r2['id']
             and a.get('name') == 'apply_patch' and a.get('target') == 'fixture_project/checkout.py']
    finals = [a for a in decoded['actions'] if a.get('turn_id') == r2['id']
              and a.get('name') == 'bash' and a.get('action_kind') == 'test'
              and 'bench_check.py' in a.get('argv', [])
              and 'final' in a.get('argv', [])]
    if len(responses) != 1 or len(edits) != 1 or len(finals) != 1:
        return
    edit, final, response = edits[0], finals[0], responses[0]
    results = {r['call_id']: r for r in decoded['results']}
    edit_result, final_result = results.get(edit['call_id']), results.get(final['call_id'])
    if not edit_result or not final_result or edit_result.get('status') != 'success' or final_result.get('status') != 'success' or final_result.get('exit_code') != 0:
        return
    milestones = [r2['sequence'], edit['sequence'], edit_result['sequence'],
                  final['sequence'], final_result['sequence'], response['sequence']]
    if milestones != sorted(set(milestones)):
        return
    records = [r['raw'] for r in decoded['records']
               if r['locator']['artifact'] == 'events.jsonl' and r2['sequence'] <= r['locator']['line'] <= response['sequence']]
    ids = [r.get('id') for r in records]
    if not all(isinstance(i, str) and i for i in ids) or len(ids) != len(set(ids)):
        return
    if any(row.get('parentId') != previous['id'] for previous, row in zip(records, records[1:])):
        return
    decoded['relations'].append({'kind': 'final_after', 'from_id': r2['id'],
        'to_id': final['id'], 'locator': final['locator'], 'native_chain_event_ids': ids})


def _add_native_snapshot_change(decoded, native):
    """Retain exact before/after digest assertions from a joined native snapshot.

    The postimage digest is read from Copilot's snapshot, never calculated
    from an observer file or a patch fragment. Missing snapshot proof leaves
    the change unresolved even when an edit action survives.
    """
    index = native / 'rewind-file-snapshots/index.json'
    if decoded['status'] != 'ok' or len(decoded['turns']) != 2 or index.is_symlink() or not index.is_file():
        return
    document = _json_bytes(index.read_bytes(), 'Copilot native snapshot index')
    if not isinstance(document, dict) or document.get('schema') != 1 or not isinstance(document.get('snapshots'), list):
        return
    r2 = decoded['turns'][1]
    snapshots = [s for s in document['snapshots'] if isinstance(s, dict) and s.get('eventId') == r2['id']]
    edits = [a for a in decoded['actions'] if a.get('turn_id') == r2['id']
             and a.get('name') == 'apply_patch' and a.get('target') == 'fixture_project/checkout.py']
    if len(snapshots) != 1 or len(edits) != 1 or not isinstance(snapshots[0].get('files'), dict) or not isinstance(snapshots[0].get('snapshotId'), str) or not snapshots[0]['snapshotId']:
        return
    edit = edits[0]
    results = [r for r in decoded['results'] if r.get('call_id') == edit['call_id'] and r.get('status') == 'success']
    paths = re.findall(r'^\*\*\* Update File: (.+)$', edit.get('patch', ''), re.M)
    if len(results) != 1 or len(paths) != 1 or not r2['sequence'] < edit['sequence'] < results[0]['sequence']:
        return
    records = [r['raw'] for r in decoded['records']
               if r['locator']['artifact'] == 'events.jsonl' and r2['sequence'] <= r['locator']['line'] <= results[0]['sequence']]
    ids = [r.get('id') for r in records]
    if not all(isinstance(i, str) and i for i in ids) or len(ids) != len(set(ids)):
        return
    if any(row.get('parentId') != previous.get('id') for previous, row in zip(records, records[1:])):
        return
    files = [f for f in snapshots[0]['files'].values() if isinstance(f, dict) and f.get('path') == paths[0]]
    if len(files) != 1:
        return
    pre, post = files[0].get('preimage'), files[0].get('postimage')
    if not all(isinstance(image, dict) and image.get('kind') == 'content'
               and image.get('resolvedPath') == paths[0]
               and isinstance(image.get('contentHash'), str)
               and re.fullmatch('[0-9a-f]{64}', image['contentHash']) for image in (pre, post)):
        return
    if pre.get('backupFile') != pre['contentHash']:
        return
    backup = native / 'rewind-file-snapshots/backups' / pre['backupFile']
    if backup.is_symlink() or not backup.is_file():
        return
    data = backup.read_bytes()
    if sha(data) != pre['contentHash'] or pre.get('size') != len(data):
        return
    # Second native derivation, recorded beside the explicit snapshot hashes:
    # the backup pre-image with the native patch applied. Credit rests on the
    # explicit hashes; this flag says whether the patch reproduces them.
    try:
        recomputed = apply_copilot_patch(data.decode('utf-8'), edit.get('patch', ''))
    except UnicodeDecodeError:
        recomputed = None
    reproduced = recomputed is not None and sha(recomputed.encode('utf-8')) == post['contentHash']
    # The snapshot is its own native record. Its locator is the document
    # (line 1) and its index in ``snapshots``; its timestamp is its own field.
    locator = {'artifact': 'rewind-file-snapshots/index.json', 'line': 1,
               'block': document['snapshots'].index(snapshots[0]), 'physical_sha256': sha(index.read_bytes())}
    decoded['records'].append({'raw': snapshots[0], 'locator': locator, 'timestamp': snapshots[0].get('timestamp')})
    decoded['file_changes'].append({'id': snapshots[0].get('snapshotId'),
        'path': edit['target'], 'before_sha256': pre['contentHash'], 'after_sha256': post['contentHash'],
        'hash_source': 'native_snapshot_hashes', 'edit_recomputed_from_backup': reproduced,
        'turn_id': r2['id'], 'action_id': edit['id'], 'call_id': edit['call_id'], 'timestamp': snapshots[0].get('timestamp'),
        'locator': locator, 'edit_locator': edit['locator'], 'result_locator': results[0]['locator']})
