"""OpenClaw public candidates: equal length, every row kept, no private value and no digest oracle."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import time

import pytest

from session_bench import openclaw_score_inputs
from session_bench.openclaw_acp_observer import turn_summary
from session_bench.openclaw_score_inputs import AFTER, BEFORE, RECEIPT, verify_state_receipt
from session_bench.openclaw_session_rows import ROWS_FILE, decode_openclaw_session_rows, read_rows
from session_bench.public_digest_check import check_public_packets, read_public_set, short_digest_warnings
from session_bench.release_score import score_release_run

ROOT = Path(__file__).resolve().parents[1]
RUNS = ('openclaw-2026-10-07-04', 'openclaw-2026-10-07-07', 'openclaw-2026-10-07-06')
PRIVATE = ROOT / 'artifacts/v1-expanded-preparation/openclaw-score-replay-v6'
PUBLIC = ROOT / 'artifacts/v1-expanded-preparation/openclaw-public-candidates-v6'
REJECTED = ROOT / 'artifacts/v1-expanded-preparation/openclaw-public-candidates-v1'
sys.path.insert(0, str(ROOT / 'scripts'))
_spec = importlib.util.spec_from_file_location('sanitize_openclaw_under_test', ROOT / 'scripts/sanitize_openclaw_score_packets.py')
sanitizer = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(sanitizer)
from test_openclaw_score_replay import ACP, CANARY, KEY, KEY_ID, PROMPTS, SID, TID, normal_session  # noqa: E402
from test_openclaw_state_capture import PRIVATE as PRIVATE_NAMES, bracket, capture, put, turn  # noqa: E402

real = pytest.mark.skipif(not (PUBLIC / 'summary.json').is_file(), reason='OpenClaw public candidates are not in this checkout')
FIXTURES = ROOT / 'tests/fixtures/openclaw_acp'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def test_row_export_keeps_every_row_and_key_at_equal_length_and_blanks_what_the_decoder_does_not_read():
    rows, schema = normal_session().export()
    texts, private = [], set()
    public, counts = sanitizer.sanitize_rows(rows, texts, private)
    assert len(public) == len(rows) and public != rows
    before, after = json.loads(rows), json.loads(public)
    # Every store, table, row, column and key stays.
    shape = lambda document: [(store['store'], table['table'], table['columns'], [sorted(row['values']) for row in table['rows']])  # noqa: E731
                              for store in document['stores'] for table in store['tables']]
    assert shape(before) == shape(after)
    text = public.decode()
    # Tables of the read: the tool and schema descriptions, the account e-mail and the mirror fingerprints.
    assert counts['tool_descriptions_blanked'] == 4 and 'List the configured agents' not in text and 'largest number of agents' not in text
    assert sorted(texts)[:1] and any('List the configured agents' in item for item in texts)
    assert counts['emails_aliased'] == 2 and 'owner.name@gmail.com' not in text and 'openai:xxxxx.xxxx@xxxxx.xxx' in text
    assert counts['fingerprints_zeroed'] > 0 and 'ab' * 16 not in text
    # The compressed event is written again: same BLOB length, same text but for the zeroed fingerprint, read by a plain reader.
    from compression import zstd
    blob = lambda document: next(row['values'] for store in document['stores'] for table in store['tables'] if table['table'] == 'transcript_events'  # noqa: E731
                                 for row in table['rows'] if row['values']['event_zstd'])
    old, new = bytes.fromhex(blob(before)['event_zstd']['blob_hex']), bytes.fromhex(blob(after)['event_zstd']['blob_hex'])
    assert len(old) == len(new) and old != new and counts['compressed_events_rewritten'] == counts['fingerprints_zeroed_in_compressed_events'] == 1
    assert zstd.decompress(new) == zstd.decompress(old).replace(b'ab' * 16, b'0' * 32) and b'ab' * 16 in zstd.decompress(old)
    assert len(zstd.decompress(new)) == blob(after)['event_utf8_bytes']
    assert '[vendor instruction text removed at equal byte length]' in text and 'agents_list' in text
    # Tables outside the read: every text value blanked, every BLOB zeroed, the ids of the test session kept.
    outside = {(store['store'].rsplit('/', 1)[-1], table['table']): table['rows'][0]['values'] for store in after['stores'] for table in store['tables']
               if table['table'] not in ('transcript_events', 'trajectory_runtime_events', 'session_windows')}
    assert outside[('openclaw-agent.sqlite', 'session_nodes')]['session_key'] == KEY and outside[('openclaw-agent.sqlite', 'session_nodes')]['current_session_id'] == SID
    assert outside[('openclaw-agent.sqlite', 'session_nodes')]['entry_json'].startswith('[value of a row outside the read removed')
    assert outside[('state_5.sqlite', 'threads')]['id'] == TID and outside[('state_5.sqlite', 'threads')]['tokens_used'] == 5
    assert outside[('openclaw.sqlite', 'acp_replay_events')]['session_id'] == ACP and outside[('openclaw.sqlite', 'acp_replay_events')]['seq'] == 1
    assert outside[('openclaw-agent.sqlite', 'session_transcript_fts')]['payload'] == {'blob_hex': '000000'}
    for value in ('user-PRIVATEACCOUNT0123456789', 'github.com/owner-account', 'skills of the owner'):
        assert value not in text
    assert {'user-PRIVATEACCOUNT0123456789', 'https://github.com/owner-account/workspace.git', 'owner.name@gmail.com'} <= private
    assert any('skills of the owner' in item for item in texts)     # the skills catalog goes to the phrase guard
    assert counts['outside_rows'] == 5 and counts['outside_blobs_zeroed'] == 1
    # The decoder gives the same facts, and density the same bytes.
    plain = lambda out: json.dumps({key: out[key] for key in ('turns', 'responses', 'actions', 'results', 'relations', 'usage', 'file_changes', 'reconciliation')},  # noqa: E731
                                   sort_keys=True).replace(out['physical_sha256'], '')
    assert plain(decode_openclaw_session_rows(rows, schema, run_canary=CANARY)) == plain(decode_openclaw_session_rows(public, schema, run_canary=CANARY))
    from session_bench.hermes_store_rows import row_bytes
    sizes = lambda document: [row_bytes(row['values']) for store in document['stores'] for table in store['tables'] for row in table['rows']]  # noqa: E731
    assert sizes(before) == sizes(after)
    with pytest.raises(ValueError, match='exporter serialization'):
        sanitizer.sanitize_rows(json.dumps(before).encode(), [], set())


def test_a_private_value_in_a_compressed_event_stops_the_sanitizer():
    from test_openclaw_score_replay import Session, USAGE, R1, R2
    session = Session()
    session.user('Read /Users/someone/notes and report. ' + 'padding ' * 10 + R1)
    session.text('x ' + R1, USAGE[0]); session.user(PROMPTS[1]); session.text('y ' + R2, USAGE[1])
    rows, _ = session.export()
    with pytest.raises(ValueError, match='compressed transcript event holds a private value'):
        sanitizer.sanitize_rows(rows, [], set())


def test_acp_stream_keeps_its_length_and_every_message_and_blanks_the_listed_commands():
    data = (FIXTURES / 'turn-r1.acp-stream.jsonl').read_bytes()
    public, count = sanitizer.sanitize_stream(data)
    assert len(public) == len(data) and count == 4 and len(public.splitlines()) == len(data.splitlines())
    assert b'Show help and common commands' not in public and b'[personal text removed' in public
    before, after = turn_summary(data), turn_summary(public)
    assert before == after and after['calls'][0]['raw_input']['command'].endswith("--run-canary SB_SURVIVAL_V1_RUN_openclaw-2026-10-07-03'")
    second = (FIXTURES / 'turn-r2.acp-stream.jsonl').read_bytes()
    assert sanitizer.sanitize_stream(second) == (second, 0)
    with pytest.raises(ValueError, match='controller serialization'):
        sanitizer.sanitize_stream(data.replace(b'{"t_ns": ', b'{"t_ns":', 1))


def test_small_documents_lose_the_operator_values_at_equal_length():
    placement = sanitizer._compact({'device': 16777234, 'files': [], 'inode': 123456789, 'path': '/Users/x/w/fixture_project', 'placed': True,
                                    'workspace_entries_before': 4321, 'workspace_listing_sha256': 'ab' * 32})
    public = sanitizer.sanitize_small_documents('run/inputs/capture/fixture-placement.json', placement)
    value = json.loads(public)
    assert len(public) == len(placement) and (value['device'], value['inode'], value['workspace_entries_before']) == (10000000, 100000000, 1000)
    assert value['workspace_listing_sha256'] == '0' * 64
    stop = sanitizer._compact({'agent_model': {'model': 'm', 'provider': 'p'}, 'output': {'stdout': {'kept': 'x', 'sha256': 'cd' * 32, 'size_bytes': 4008}}, 'stopped': True})
    public = json.loads(sanitizer.sanitize_small_documents('run/inputs/capture/gateway-stop.json', stop))
    assert public['output']['stdout'] == {'kept': 'x', 'sha256': '0' * 64, 'size_bytes': 1000} and public['agent_model'] == {'model': 'm', 'provider': 'p'}
    assert sanitizer.sanitize_small_documents('run/inputs/capture/plan.json', b'{"a":1}\n') == b'{"a":1}\n'


def test_inventories_alias_every_unclassified_name_and_the_receipt_still_verifies(tmp_path, monkeypatch):
    monkeypatch.setattr(openclaw_score_inputs, 'STATE_ROOT_SUFFIX', '/openclaw-home')
    root, stores, before, detail, started = bracket(tmp_path)
    # Many names of one length in one directory: an alias must never be given twice.
    for number in range(150):
        put(root, f'credentials/token-{number:03d}.json', b'private')
    # A cache file that the Codex backend names by a digest of the account: it changes during the turn, so the receipt classifies it.
    digest_name = hashlib.sha1(b'{"account_id":"a","chatgpt_user_id":"u","is_workspace_account":false}').hexdigest() + '.json'
    cache = 'agents/main/agent/codex-home/cache/codex_apps_tools/' + digest_name
    put(root, cache, b'old cache')
    put(root, 'cache/control-ui-assets/' + 'ab' * 32 + '/index.js', b'asset')
    before = sanitizer.json.loads(sanitizer._compact(__import__('session_bench.openclaw_state_capture', fromlist=['x']).inventory_openclaw_home(root)))
    started = time.time_ns()
    turn(root, stores)
    put(root, cache, b'new cache, changed in the turn')
    receipt, after = capture(root, tmp_path / 'r2-native', before, started, turn=2)
    assert cache in receipt['accounting']['shared_entries_changed_content_not_read']
    documents = {RECEIPT: sanitizer._compact(receipt), BEFORE: sanitizer._compact(before), AFTER: sanitizer._compact(after)}
    public, foreign, kept = sanitizer.alias_inventories(documents)
    assert all(len(public[name]) == len(documents[name]) for name in documents)
    new_receipt, new_before, new_after = (json.loads(public[name]) for name in (RECEIPT, BEFORE, AFTER))
    verify_state_receipt(new_receipt, new_before, new_after)
    names = [entry['relative_path'] for entry in new_after['entries']]
    assert len(set(names)) == len(names) == len(after['entries'])
    parts = {part for name in names + [entry['relative_path'] for entry in new_before['entries']] for part in name.split('/')}
    for private in (*PRIVATE_NAMES, 'credentials/token-007.json', 'identity/device.json'):
        leaf = private.split('/')[-1]
        assert leaf not in parts or leaf in kept, private
    assert not {'credentials', 'identity', 'cron', '.env', 'exec-approvals.json', 'token-007.json'} & parts
    assert 'credentials' in foreign and 'openclaw-agent.sqlite' in kept and 'x' not in sanitizer._DIGITS
    # A name with a long hex run is aliased also when the receipt classifies its entry; its directory keeps its name.
    everything = b''.join(public.values())
    assert digest_name.encode() not in everything and digest_name[:40].encode() not in everything and (b'ab' * 32) not in everything
    assert digest_name in foreign and digest_name not in kept and 'codex_apps_tools' in kept
    changed = [path for path in new_receipt['accounting']['shared_entries_changed_content_not_read'] if '/cache/codex_apps_tools/' in path]
    assert len(changed) == 1 and len(changed[0]) == len(cache) and changed[0] in names
    # No digest of a digested subtree is published: zeros, or the state word where the two inventories differ.
    for inventory, word in ((new_before, '0' * 64), (new_after, None)):
        for entry in inventory['entries']:
            if entry['kind'] == 'directory-digest':
                values = {entry['entries_sha256'], *entry['directory_sha256'].values()}
                assert values <= {'0' * 64, sanitizer.CHANGED} and (word is None or values == {word})
    assert len(sanitizer.CHANGED) == 64 and sanitizer.CHANGED.startswith('changed_')
    assert any(entry['kind'] == 'directory-digest' for entry in new_after['entries'])
    from session_bench.public_digest_check import long_hex_runs
    assert long_hex_runs({name: data for name, data in public.items()}, public_references={})['unlisted'] == []
    assert len(long_hex_runs({name: data for name, data in documents.items()}, public_references={})['unlisted']) == 4
    # The copied files lose digest and file identity; the files of the thread keep their sizes; the store copies do not.
    assert all(row['sha256'] == '0' * 64 for row in new_receipt['artifacts'])
    assert [row['size_bytes'] for row in new_receipt['artifacts']] == [row['size_bytes'] for row in receipt['artifacts']]
    copied = [item for store in new_receipt['session_store']['stores'] for item in store['copied_files']]
    assert copied and all(item['sha256'] == '0' * 64 and str(item['size_bytes']).strip('0') == '1' for item in copied)
    assert new_receipt['classes']['session_owned'] == receipt['classes']['session_owned']


def test_alias_digits_do_not_hold_the_padding_letter():
    assert 'x' not in sanitizer._DIGITS and len(set(sanitizer._DIGITS)) == 61


# --- The real public set ---

@pytest.fixture(scope='module')
def candidates():
    if not (PUBLIC / 'summary.json').is_file():
        pytest.skip('OpenClaw public candidates are not in this checkout')
    return json.loads((PUBLIC / 'summary.json').read_bytes())


@real
def test_public_candidates_keep_the_31_metric_rows(candidates):
    assert [row['packet'] for row in candidates['runs']] == list(RUNS) and all(row['all_31_metric_rows_unchanged'] for row in candidates['runs'])
    for run in RUNS:
        private = json.loads((PRIVATE / f'{run}-receipt.json').read_bytes())['diagnostics']['intact']['metrics']
        public = json.loads((PUBLIC / f'{run}-receipt.json').read_bytes())['diagnostics']['intact']['metrics']
        assert private == public and len(public) == 31
        assert json.loads((PUBLIC / f'{run}-tamper.json').read_bytes())['status'] == 'passed'
    bundle = json.loads((PUBLIC / 'public-inputs-candidate.json').read_bytes())
    assert bundle['configuration_id'] == 'openclaw' and len(bundle['pairs']) == 3
    assert sha((PUBLIC / 'public-inputs-candidate.json').read_bytes()) == candidates['public_bundle_sha256']
    for pair in bundle['pairs']:
        score_release_run(pair['survival'], pair['format'])


@real
def test_every_file_keeps_its_byte_length_and_the_rows_keep_every_row_and_key(candidates):
    for run in RUNS:
        for path in sorted(item for folder in ('native', 'inputs') for item in (PRIVATE / run / folder).rglob('*') if item.is_file()):
            relative = path.relative_to(PRIVATE / run)
            assert (PUBLIC / run / relative).stat().st_size == path.stat().st_size, relative
        shape = lambda base: [(store['store'], table['table'], [sorted(row['values']) for row in table['rows']])  # noqa: E731
                              for store in json.loads((base / run / 'native/capture' / ROWS_FILE).read_bytes())['stores'] for table in store['tables']]
        assert shape(PRIVATE) == shape(PUBLIC)
        # No file of the Codex thread and no gateway output is in a packet.
        names = [path.name for path in (PUBLIC / run).rglob('*') if path.is_file()]
        assert not [name for name in names if name.startswith('rollout-') or name.endswith('.sh') or 'gateway-std' in name]


@real
def test_public_set_holds_no_private_value_and_only_bound_digests(candidates):
    public_set = read_public_set([PUBLIC / run for run in RUNS], [PUBLIC / 'public-inputs-candidate.json'])
    home = Path.home()
    forbidden = sanitizer.encodings(home.name)
    for run in RUNS:
        rows = json.loads((PRIVATE / run / 'native/capture' / ROWS_FILE).read_bytes())
        threads = next(table for store in rows['stores'] for table in store['tables'] if table['table'] == 'threads')['rows'][0]['values']
        forbidden |= {threads[key].encode() for key in ('creator_user_id', 'creator_account_id', 'git_origin_url') if isinstance(threads.get(key), str)}
        forbidden |= {match.group(0) for match in sanitizer._EMAIL.finditer((PRIVATE / run / 'native/capture' / ROWS_FILE).read_bytes())}
    assert len(forbidden) > 8
    for name, data in public_set.items():
        assert not sanitizer.private_markers(data), name
        assert not [value for value in forbidden if value in data], name
    assert short_digest_warnings(public_set) == []
    receipts = [json.loads((PUBLIC / f'{run}-receipt.json').read_bytes()) for run in RUNS]
    result = check_public_packets([PUBLIC / run for run in RUNS], receipts=receipts, extra_files=[PUBLIC / 'public-inputs-candidate.json'],
                                  allowlist=sanitizer.DIGEST_ALLOWLIST, hex_allowlist=sanitizer.HEX_ALLOWLIST)
    assert result['unbound'] == [] and result['classes']['public_bytes'] > 0
    # Long hex runs: zeroed fingerprints, provider ids and the three rewritten frames; no hex name in an inventory.
    assert result['hex_runs']['unlisted'] == [] and result['hex_runs']['classes']['allowlisted'] == 3
    for run in RUNS:
        rows = json.loads((PRIVATE / run / 'native/capture' / ROWS_FILE).read_bytes())
        threads = next(table for store in rows['stores'] for table in store['tables'] if table['table'] == 'threads')['rows'][0]['values']
        name = hashlib.sha1(('{"account_id":%s,"chatgpt_user_id":%s,"is_workspace_account":false}' % (
            json.dumps(threads['creator_account_id']), json.dumps(threads['creator_user_id']))).encode()).hexdigest().encode()
        assert name in (PRIVATE / run / 'inputs/capture/state-before.json').read_bytes()    # the rule of the cache file name holds
        assert not [label for label, data in public_set.items() if name in data]
        # The compressed event holds no fingerprint any more and reads with a plain Zstandard reader.
        from compression import zstd
        public_rows = json.loads((PUBLIC / run / 'native/capture' / ROWS_FILE).read_bytes())
        blobs = [row['values']['event_zstd']['blob_hex'] for store in public_rows['stores'] for table in store['tables'] if table['table'] == 'transcript_events'
                 for row in table['rows'] if row['values']['event_zstd']]
        assert len(blobs) == 1 and b'"mirrorSourceFingerprint":"' + b'0' * 32 in zstd.decompress(bytes.fromhex(blobs[0]))


@pytest.mark.skipif(not (REJECTED / 'summary.json').is_file(), reason='the rejected OpenClaw set is not in this checkout')
def test_the_rejected_set_v1_fails_the_hex_run_check():
    from session_bench.public_digest_check import CONFIGURATION_HEX_ALLOWLIST, PROVIDER_ID_FIELDS, long_hex_runs
    hits = long_hex_runs(read_public_set([REJECTED / run for run in RUNS]), hex_allowlist=[*sanitizer.HEX_ALLOWLIST, *CONFIGURATION_HEX_ALLOWLIST['openclaw']],
                         provider_id_fields=PROVIDER_ID_FIELDS['openclaw'])['unlisted']
    # The 24 cache names that are SHA-1 digests of the account are among the unlisted runs (the hardened gate may find more).
    names = [item for item in hits if item['length'] == 40]
    assert len(names) == 24
    assert all('/cache/codex_apps_' in item['before'] or 'odex_apps_' in item['before'] for item in names)


@real
def test_metric_rows_of_v3_equal_the_rows_of_the_rejected_set_v1_except_reconciliation(candidates):
    if not (REJECTED / 'summary.json').is_file():
        pytest.skip('the rejected OpenClaw set is not in this checkout')
    for run in RUNS:
        old = json.loads((REJECTED / f'{run}-receipt.json').read_bytes())['diagnostics']['intact']['metrics']
        new = json.loads((PUBLIC / f'{run}-receipt.json').read_bytes())['diagnostics']['intact']['metrics']
        # Set v3 removes the reconciliation credit (owner decision 2026-10-08); the other 30 rows are those of v1.
        rest = lambda rows: [row for row in rows if row['id'] != 'attribution.reconciliation']  # noqa: E731
        assert rest(old) == rest(new)
        assert {row['id']: row['state'] for row in new}['attribution.reconciliation'] == 'native_absent'
        assert {row['id']: row['state'] for row in old}['attribution.reconciliation'] == 'measured'
    for run in RUNS:
        read, others, _, _, _ = read_rows(*((PUBLIC / run / 'native/capture' / name).read_bytes() for name in (ROWS_FILE, 'session-store-schema.json')))
        for _store, _table, table_rows in others:
            for row in table_rows:
                for value in row['values'].values():
                    assert not isinstance(value, str) or not value or value.startswith('[value of a row outside the read'[:len(value)]) or value in (
                        json.loads((PUBLIC / run / 'native/capture' / ROWS_FILE).read_bytes())[key] for key in ('session_id', 'thread_id', 'session_key', 'key_id', 'acp_session_id'))


@real
def test_the_machine_values_tables_of_the_adapter_document_are_those_of_the_public_set():
    """The tables of 'Machine values that stay' are computed from the public inventories and receipts; the document cannot drift."""
    spec = importlib.util.spec_from_file_location('tabulate_openclaw_machine_values', ROOT / 'scripts/tabulate_openclaw_machine_values.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    table = module.tables(PUBLIC)
    document = (ROOT / 'docs/survival-v1/adapters/openclaw.md').read_text()
    assert table in document
    # The bound copy in every packet is the same document.
    for packet in sorted(path for path in PUBLIC.iterdir() if (path / 'manifest.json').is_file()):
        assert (packet / 'runtime/docs/survival-v1/adapters/openclaw.md').read_text() == document
    # The facts the review found, stated as numbers of the data.
    rows = {line.split('|')[1].strip(): [cell.strip() for cell in line.split('|')[2:-1]] for line in table.splitlines() if line.startswith('| ') and '---' not in line}
    assert rows['session-store file in no class'][:1] == ['2'] and rows['directory above a classified entry, in no class'][:1] == ['28']
    assert rows['directory in a receipt class'][:1] == ['36'] and rows['digested root, in no receipt class'][:1] == ['6']
    assert rows['file in a receipt class'][2:4] == ['none', 'none'] and rows['every other unclassified entry'][2:4] == ['none', 'none']
