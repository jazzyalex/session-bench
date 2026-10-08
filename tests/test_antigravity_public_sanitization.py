"""The public Antigravity derivative keeps the metric rows and publishes no private value or digest."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


builder = load('build_antigravity_score_replays')
sanitizer = load('sanitize_antigravity_score_packets')
sha = lambda data: hashlib.sha256(data).hexdigest()
SESSION = '11111111-2222-3333-4444-555555555555'


def entry(path, kind='file', **changes):
    return {'relative_path': path, 'kind': kind, 'device': 16777233, 'inode': 700 + len(path), 'size_bytes': 4321,
            'birth_ns': 1790000000123456789, 'ctime_ns': 1790000000223456789, 'mtime_ns': 1790000000323456789, **changes}


def inventory_documents():
    from session_bench.antigravity_root_evidence import _state_statement, classify_state_changes, inventory_sha256
    primary = f'brain/{SESSION}/.system_generated/logs/transcript.jsonl'
    chain = [entry('brain', 'directory'), entry(f'brain/{SESSION}', 'directory'), entry(f'brain/{SESSION}/.system_generated', 'directory'),
             entry(f'brain/{SESSION}/.system_generated/logs', 'directory'), entry(primary)]
    old = [entry('brain', 'directory'), entry('brain/aaaaaaaa-old-conversation', 'directory'), entry('brain/aaaaaaaa-old-conversation/my-private-plan.md'),
           entry('cache', 'directory'), entry('cache/last_conversations.json'), entry('cache/private-cache-name.bin', birth_ns=None),
           entry('installation_id'), entry('cli.log', 'symlink', target_sha256=hashlib.sha256(b'log/cli-of-an-older-run.log').hexdigest())]
    new = [entry('brain', 'directory', mtime_ns=5), *old[1:4], entry('cache/last_conversations.json', size_bytes=9), *old[5:7], entry('cli.log', 'symlink', inode=9, target_sha256=hashlib.sha256(b'log/cli-1.log').hexdigest()), *chain[1:]]
    inventory = lambda entries: {'root': '/Users/someone/.gemini/antigravity-cli', 'root_identity': {'device': 1, 'inode': 2},
                                 'entries': sorted(entries, key=lambda row: row['relative_path']), 'metadata_only': True, 'scope': 'whole_cli_state_directory'}
    before, after = inventory(old), inventory(new)
    digest = inventory_sha256(after)
    classes = classify_state_changes(before, after, started_ns=0, conversation_id=SESSION)
    receipt = {'conversation_id': SESSION, 'started_ns': 0, 'session_directories': classes['session_directories'],
               'classes': {name: classes[name] for name in ('session_owned', 'run_owned', 'shared_changed', 'directories_changed')},
               'accounting': {'changed_entries': classes['changed_entries'], 'statement': _state_statement(classes),
                              'shared_stores_changed_content_not_read': sorted(row['relative_path'] for row in classes['shared_changed'])}, 'before_inventory_sha256': inventory_sha256(before), 'after_inventory_sha256': digest,
               'quiescence': {'inventory_sha256': ['f' * 64, digest, digest]},
               'artifacts': [{'relative_path': 'log/cli-1.log', 'private_only': True, 'sha256': 'a' * 64},
                             {'relative_path': primary, 'private_only': False, 'sha256': 'b' * 64}]}
    pretty = lambda value: json.dumps(value, sort_keys=True, indent=2).encode() + b'\n'
    return {sanitizer.RECEIPT: pretty(receipt), sanitizer.BEFORE: pretty(before), sanitizer.AFTER: pretty(after)}


def test_unchanged_entries_of_the_state_directory_are_aliased_and_classified_entries_keep_their_names():
    from session_bench.antigravity_root_evidence import classify_state_changes, inventory_sha256
    original = inventory_documents()
    public = sanitizer.alias_inventories(original)
    assert all(len(public[name]) == len(data) for name, data in original.items())
    everything = b''.join(public.values())
    assert b'old-conversation' not in everything and b'my-private-plan' not in everything and b'private-cache-name' not in everything and b'installation_id' not in everything
    receipt, before, after = (json.loads(public[name]) for name in (sanitizer.RECEIPT, sanitizer.BEFORE, sanitizer.AFTER))
    names = {row['relative_path'] for row in after['entries']}
    # The changed shared store, its directory and the captured conversation keep their names.
    assert {'cache', 'cache/last_conversations.json', 'brain', f'brain/{SESSION}'} <= names and len(names) == len(after['entries'])
    hidden = [row for row in after['entries'] if set(row['relative_path'].replace('/', '')) <= set(sanitizer._ALPHABET) and 'x' in row['relative_path'] and SESSION not in row['relative_path'] and not row['relative_path'].startswith('cache/last')]
    assert len(hidden) == 4 and all(row['size_bytes'] == 1000 and row['mtime_ns'] == 10 ** 18 for row in hidden)
    assert any(row['relative_path'].startswith('cache/') and row['birth_ns'] is None for row in hidden)
    # The public receipt binds the public inventories, and the classification is the same.
    assert receipt['before_inventory_sha256'] == inventory_sha256(before) and receipt['after_inventory_sha256'] == inventory_sha256(after)
    assert receipt['quiescence']['inventory_sha256'] == ['0' * 64, receipt['after_inventory_sha256'], receipt['after_inventory_sha256']]
    assert [row['sha256'] for row in receipt['artifacts']] == ['0' * 64, 'b' * 64]
    # A link target is a log file name, which is easy to guess: its hash is zeroed in both inventories.
    links = [row for inventory in (before, after) for row in inventory['entries'] if row['kind'] == 'symlink']
    assert len(links) == 2 and all(row['target_sha256'] == '0' * 64 for row in links)
    assert hashlib.sha256(b'log/cli-of-an-older-run.log').hexdigest().encode() not in everything
    # The receipt describes the public inventories: its shared-store digests are recomputed from them.
    assert receipt['classes']['shared_changed'] == classify_state_changes(before, after, started_ns=0, conversation_id=SESSION)['shared_changed']
    private = {name: json.loads(data) for name, data in original.items()}
    def view(b, a):
        classes = classify_state_changes(b, a, started_ns=0, conversation_id=SESSION)
        return {**classes, 'shared_changed': [(row['relative_path'], row['kind'], row['change']) for row in classes['shared_changed']]}
    assert view(before, after) == view(private[sanitizer.BEFORE], private[sanitizer.AFTER])


def test_database_rewrite_blanks_private_blobs_removes_stale_pages_and_keeps_rows_and_size(tmp_path):
    import sqlite3
    from session_bench.antigravity_conversation_db import encode, field, read_conversation_db, text
    path = tmp_path / 'c.db'
    connection = sqlite3.connect(path)
    connection.executescript('PRAGMA page_size=4096; CREATE TABLE gen_metadata (idx integer, data blob, size integer, PRIMARY KEY (idx));'
                             'CREATE TABLE steps (idx integer, metadata blob, step_payload blob, PRIMARY KEY (idx));'
                             'CREATE TABLE trajectory_metadata_blob (id text, data blob, PRIMARY KEY (id)); PRAGMA user_version=1;')
    secret = 'operator rule: always deploy from /Users/someone/work ' * 400
    generation = lambda prompt: encode([(1, [(1, prompt), (16, [(1, 'identity'), (2, 'You are Antigravity.')]), (16, [(1, 'user_rules'), (2, 'my rule')]),
                                             (4, [(7, 'bot-1'), (8, [(1, 'sessionID'), (2, '-1234567890123456789')])]), (19, 'model-x')]), (8, b'\x88\x9c' * 40)])
    connection.execute('INSERT INTO gen_metadata VALUES (0,?,0)', (generation(secret),))
    connection.execute('INSERT INTO steps VALUES (0,?,?)', (encode([(9, [(2, 5), (8, [(1, 'sessionID'), (2, '-1234567890123456789')])])]),
                                                           encode([(19, [(2, 'see /Users/someone/work and octocat')])])))
    connection.execute('INSERT INTO trajectory_metadata_blob VALUES (?,?)', ('main', encode([(1, [(3, [(1, 'octocat/project')])]), (15, b'\x88\x9c' * 40)])))
    connection.commit()
    # An update leaves the old, larger row in free pages.
    connection.execute('UPDATE gen_metadata SET data=? WHERE idx=0', (generation('short prompt with my rule'),))
    connection.commit(); connection.close()
    database = path.read_bytes()
    assert b'always deploy' in database
    private = sanitizer.database_private_values(database)
    assert private == {'accounts': {'octocat'}, 'numbers': {b'-1234567890123456789'}, 'blobs': {b'\x88\x9c' * 40}}
    public, written = sanitizer.sanitize_database(database, [(b'/Users/someone', b'/Users/xxxxxxx'), (b'octocat', b'xxxxxxx')])
    assert len(public) == len(database) and written == 4
    for value in (b'always deploy', b'my rule', b'someone', b'octocat', b'1234567890123456789', b'\x88\x9c\x88\x9c'):
        assert value not in public
    rows = read_conversation_db(public)['tables']
    data = rows['gen_metadata'][0]['data']
    assert text(data, 1, 19) == 'model-x' and text(data, 1, 4, 7) == 'bot-1' and b'You are Antigravity.' in data and len(data) == len(generation('short prompt with my rule'))
    assert text(rows['steps'][0]['step_payload'], 19, 2) == 'see /Users/xxxxxxx/work and xxxxxxx' and field(rows['steps'][0]['metadata'], 9, 2) == [5]
    # No byte outside the live cells survives: deleted rows, free pages and page slack are zero.
    connection = sqlite3.connect(path)
    connection.execute('INSERT INTO steps VALUES (5,?,?)', (b'deleted-row-marker' * 300, b'x'))
    connection.commit()
    connection.execute('DELETE FROM steps WHERE idx=5')
    connection.commit(); connection.close()
    assert b'deleted-row-marker' in path.read_bytes()
    scrubbed, _ = sanitizer.sanitize_database(path.read_bytes(), [])
    assert b'deleted-row-marker' not in scrubbed and sanitizer.scrub_free_space(scrubbed) == scrubbed and len(scrubbed) == len(path.read_bytes())
    assert [row['idx'] for row in read_conversation_db(scrubbed)['tables']['steps']] == [0]
    assert scrubbed == sanitizer.sanitize_database(scrubbed, [])[0]
    assert sanitizer.blank_leaves(encode([(1, 'secret text'), (2, 7), (3, [(1, 'inner')]), (4, b'\x00\xff')])) == encode([(1, 'x' * 11), (2, 7), (3, [(1, 'xxxxx')]), (4, b'\x00\x00')])


def test_extract_alias_reaches_hex_encoded_values_at_equal_length():
    from session_bench.native_replay import canonical
    blob = b'\x0a\x20file:///Users/someone/work/octocat'
    data = canonical({'row': {'workspace_uris': '["file:///Users/someone/work"]', 'raw_summary': {'hex': blob.hex()}}, 'entries': [blob.hex()]})
    public = sanitizer.alias_extract(data, [(b'/Users/someone', b'/Users/xxxxxxx'), (b'octocat', b'xxxxxxx')])
    document = json.loads(public)
    assert len(public) == len(data) and b'someone' not in public and b'someone'.hex().encode() not in public and b'octocat'.hex().encode() not in public
    assert bytes.fromhex(document['entries'][0]) == b'\x0a\x20file:///Users/xxxxxxx/work/xxxxxxx' and 'xxxxxxx/work' in document['row']['workspace_uris']


def test_home_name_cut_in_two_by_a_stream_text_delta_is_blanked_at_equal_length():
    line = lambda step, text: json.dumps({'event': 'step_update', 'step_update': {'step_index': step, 'text_delta': text}},
                                         separators=(',', ':'), ensure_ascii=False).encode()
    # The stream writes ``&`` as an escape; the alias must leave every other byte alone.
    first = line(7, 'café "x" & see /Users/some').replace(b'&', b'\\u0026')
    data = b'\n'.join([first, line(9, 'one is not in step 7'), line(7, 'one/project and someone again'), b''])
    public, count = sanitizer.alias_stream_text(data, 'someone')
    assert count == 2 and len(public) == len(data)
    rows = [json.loads(row) for row in public.splitlines()]
    assert rows[0]['step_update']['text_delta'] + rows[2]['step_update']['text_delta'] == 'café "x" & see /Users/xxxxxxx/project and xxxxxxx again'
    assert public.splitlines()[0].count(b'\\u0026') == 1 and rows[1]['step_update']['text_delta'] == 'one is not in step 7'


def test_private_marker_scan_finds_a_home_path_an_email_and_a_credential():
    assert sanitizer.private_markers(b'{"cwd":"/Users/xxxxx/Repository"}') == []
    assert 'home path' in sanitizer.private_markers(b'Cwd=/Users/someone/project')
    assert 'e-mail' in sanitizer.private_markers(b'account someone@gmail.com')
    assert 'credential' in sanitizer.private_markers(b'token ya29.' + b'a' * 40)


@pytest.fixture(scope='module')
def candidates(tmp_path_factory):
    base = tmp_path_factory.mktemp('antigravity-public')
    builder.build(base / 'private')
    return base / 'private', base / 'public', sanitizer.build(base / 'private', base / 'public')


def _forms(data):
    out = {sha(data), sha(data.rstrip(b'\n')), sha(data + b'\n')}
    try:
        text = json.dumps(json.loads(data), sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()
        out |= {sha(text), sha(text + b'\n')}
    except ValueError:
        pass
    return out


def test_public_candidates_keep_metric_rows_and_leak_no_private_value_or_digest(candidates):
    from session_bench.antigravity_conversation_db import read_conversation_db
    from session_bench.antigravity_root_evidence import inventory_sha256
    private, public, summary = candidates
    assert [row['all_31_metric_rows_unchanged'] for row in summary['runs']] == [True, True, True]
    bundle = public / 'public-inputs-candidate.json'
    assert sha(bundle.read_bytes()) == summary['public_bundle_sha256']
    document = json.loads(bundle.read_bytes())
    assert document['configuration_id'] == 'antigravity' and [pair['survival']['repetition'] for pair in document['pairs']] == [1, 2, 3]
    files = {path.relative_to(public).as_posix(): path.read_bytes() for path in public.rglob('*') if path.is_file()
             and not path.name.endswith('-private-transformation.json') and path.name != 'summary.json'}
    home = Path.home().name.encode()
    # The private values that the private databases name must be gone in every encoding the packet uses.
    forbidden = set()
    for run in sanitizer.RUNS:
        database = next((private / run / 'native/capture/conversations').glob('*.db')).read_bytes()
        values = sanitizer.database_private_values(database)
        forbidden |= {name.encode() for name in values['accounts']} | {name.lower().encode() for name in values['accounts']} | values['numbers'] | values['blobs']
    assert len(forbidden) >= 3
    everything = b'\n'.join(files.values())
    for name, data in files.items():
        assert sanitizer.private_markers(data, forbidden) == [], name
        assert not name.split('/')[1:2] == ['inputs'] or 'run-owned-private' not in name
    assert not [value for value in forbidden if len(value) < 64 and value.hex().encode() in everything]
    assert all((public / run / 'inputs/capture/shared-store-extract.json').is_file() for run in sanitizer.RUNS)
    assert home.hex().encode() not in everything and b'run-owned-private/' not in b'\n'.join(name.encode() for name in files)
    public_hex = set(re.findall(rb'[0-9a-f]{64}', everything))
    originals = set()
    for run in sanitizer.RUNS:
        for path in (private / run).rglob('*'):
            if not path.is_file():
                continue
            data, twin = path.read_bytes(), files.get(f'{run}/{path.relative_to(private / run).as_posix()}')
            if twin == data:
                continue
            originals |= _forms(data)
            if path.suffix != '.db':
                kept = set((twin or b'').split(b'\n'))
                for line in data.split(b'\n'):
                    if line and line not in kept:
                        originals |= _forms(line)
        receipt = json.loads((private / f'{run}-receipt.json').read_bytes())
        originals |= {receipt['manifest_sha256'], receipt['diagnostics_sha256']}
        # Digests of the private inventories and of the private run files are not public.
        capture = private / run / 'inputs/capture'
        state = json.loads((capture / 'r2-native-receipt.json').read_bytes())
        originals |= {state['before_inventory_sha256'], state['after_inventory_sha256'], *state['quiescence']['inventory_sha256']}
        originals |= {row['sha256'] for row in state['artifacts'] if row['private_only']}
        originals |= {inventory_sha256(json.loads((capture / name).read_bytes())) for name in ('state-before.json', 'r2-state-after.json')}
    assert len(originals) > 100 and not {digest for digest in originals if digest.encode() in public_hex}
    # Putting the real home name back into public bytes rebuilds no published digest.
    rebuilt = set()
    for data in files.values():
        for candidate in (data.replace(b'/Users/' + b'x' * len(home), b'/Users/' + home), data.replace(b'x' * len(home), home)):
            if candidate != data:
                rebuilt |= _forms(candidate)
                if len(data) < 2_000_000 and b'SQLite format 3' not in data[:16]:
                    rebuilt |= {digest for old, new in zip(data.split(b'\n'), candidate.split(b'\n')) if old != new for digest in _forms(new)}
    assert not {digest for digest in rebuilt if digest.encode() in public_hex}
    for run in sanitizer.RUNS:
        before = {path.relative_to(private / run).as_posix(): path for path in (private / run / 'native').rglob('*') if path.is_file()}
        after = {path.relative_to(public / run).as_posix(): path for path in (public / run / 'native').rglob('*') if path.is_file()}
        assert set(before) == set(after) and all(before[name].stat().st_size == after[name].stat().st_size for name in before)
        # Every database row stays, with equal BLOB lengths; the decoded step text is equal apart from the aliases.
        name = next(item for item in before if item.endswith('.db'))
        left, right = read_conversation_db(before[name].read_bytes()), read_conversation_db(after[name].read_bytes())
        assert left['user_version'] == right['user_version'] and set(left['tables']) == set(right['tables'])
        for table, rows in left['tables'].items():
            assert [{key: len(value) if isinstance(value, bytes) else value for key, value in row.items()} for row in rows] == [
                {key: len(value) if isinstance(value, bytes) else value for key, value in row.items()} for row in right['tables'][table]]
        assert not (public / run / 'inputs/public-transformation.json').read_bytes().count(b'original_sha256')
        for item in ('capture-start.json', 'state-before.json', 'r2-state-after.json', 'r2-native-receipt.json'):
            assert (public / run / 'inputs/capture' / item).stat().st_size == (private / run / 'inputs/capture' / item).stat().st_size
