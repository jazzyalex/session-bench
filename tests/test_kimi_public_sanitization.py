"""Kimi public candidates: equal length, every record kept, no private value and no digest oracle."""
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sys

import pytest

from session_bench.kimi_wire_records import SESSION_DIR, WIRE, decode_kimi_session, read_kimi_family, read_wire
from session_bench.public_digest_check import check_public_packets, long_hex_runs, read_public_set, short_digest_warnings
from session_bench.release_score import score_release_run

ROOT = Path(__file__).resolve().parents[1]
RUNS = ('kimi-2026-10-08-01', 'kimi-2026-10-08-02', 'kimi-2026-10-08-06')
PRIVATE = ROOT / 'artifacts/v1-expanded-preparation/kimi-score-replay-v4'
PUBLIC = ROOT / 'artifacts/v1-expanded-preparation/kimi-public-candidates-v4'
sys.path.insert(0, str(ROOT / 'scripts'))
_spec = importlib.util.spec_from_file_location('sanitize_kimi_under_test', ROOT / 'scripts/sanitize_kimi_score_packets.py')
sanitizer = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(sanitizer)
from test_kimi_score_replay import SYSTEM, compact, normal_session  # noqa: E402

real = pytest.mark.skipif(not (PUBLIC / 'summary.json').is_file(), reason='Kimi public candidates are not in this checkout')
ORG, KEY = b'org-0123456789abcdef0123456789abcdef', b'ak-abcdefghij0123456789'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def test_wire_keeps_every_record_and_key_at_equal_length_and_blanks_the_vendor_text():
    session = normal_session(retry=True)
    wire = session.wire()
    texts, private = [], set()
    public, counts = sanitizer.sanitize_wire(wire, texts, private)
    assert len(public) == len(wire) and public != wire
    before, after = read_wire(wire), read_wire(public)
    keys = lambda value: sorted((key, keys(item)) for key, item in value.items()) if isinstance(value, dict) else (  # noqa: E731
        [keys(item) for item in value] if isinstance(value, list) else None)
    assert [(number, keys(row)) for number, row in before] == [(number, keys(row)) for number, row in after]
    assert [len(compact(row)) for _, row in before] == [len(compact(row)) for _, row in after]
    text = public.decode()
    # The first sentence of the system prompt stays (it names the harness); the rest is blanked.
    assert "You are Kimi Code CLI, an interactive general AI agent running on a user's computer." in text
    assert 'vendor rules of this long instruction' not in text and 'Run a shell command with care' not in text
    assert 'The shell command line to run here' not in text and 'Injected vendor reminder' not in text
    assert counts == {'system_prompts_blanked': 1, 'tool_descriptions_blanked': 4, 'injected_messages_blanked': 2, 'vendor_digests_zeroed': 20,
                      'time_zones_blanked': 0, 'local_dates_blanked': 0}
    assert sanitizer.VENDOR_NOTICE.decode() in text and len(texts) == 7 and SYSTEM[SYSTEM.index('.') + 1:] in texts
    assert not re.search(r'"(?:hash|systemPromptHash|toolsHash)":"(?!0{64})', text)
    # Tool names, prompts, calls, results, usage and ids stay: the decode is the same.
    strip = lambda out: {key: value for key, value in out.items() if key not in ('diagnostics',)}  # noqa: E731
    assert strip(decode_kimi_session(public, session.state())) == strip(decode_kimi_session(wire, session.state()))
    # The guard finds a phrase of a blanked text in any file of a packet.
    phrases = sanitizer.instruction_phrases(texts)
    assert phrases and sanitizer.require_no_instruction_phrase({'run/native/wire.jsonl': public}, phrases) == len(phrases)
    with pytest.raises(ValueError, match='still in 1 public file'):
        sanitizer.require_no_instruction_phrase({'run/native/wire.jsonl': public, 'run/inputs/copy.json': wire}, phrases)


def test_time_zone_and_local_date_of_an_injected_date_message_are_blanked_and_a_message_of_unknown_origin_stops_the_sanitizer():
    session = normal_session()
    row = next(row for row in session.rows if row.get('type') == 'context.append_message' and row['message']['origin']['kind'] == 'injection')
    row['message']['origin'].update(variant='date_change', disclosure={'kind': 'date', 'localDate': '2026-10-07', 'timeZone': 'Europe/Synthetic_City'})
    private = set()
    public, counts = sanitizer.sanitize_wire(session.wire(), [], private)
    assert counts['time_zones_blanked'] == counts['local_dates_blanked'] == 1 and private == {'Europe/Synthetic_City'}
    assert b'Synthetic_City' not in public and b'2026-10-07' not in public and b'localDate' in public
    assert len(public) == len(session.wire())
    row['message']['origin'] = {'kind': 'something-new'}
    with pytest.raises(ValueError, match='unknown origin'):
        sanitizer.sanitize_wire(session.wire(), [], set())
    spaced = session.wire().replace(b'{"type":"metadata",', b'{"type": "metadata",', 1)
    with pytest.raises(ValueError, match='compact serialization'):
        sanitizer.sanitize_wire(spaced, [], set())


def test_organisation_and_key_ids_are_aliased_at_equal_length_in_the_wire_the_log_and_the_stream():
    session = normal_session(retry=True)
    log = b'2026-10-08T02:22:50.683Z WARN  llm request failed  errorMessage="429 Your account ' + ORG + b'<' + KEY + b'> request reached"\n'
    for data in (session.wire(), session.stdout(2), log):
        private = set()
        public, count = sanitizer.alias_accounts(data, private)
        assert count >= 2 and len(public) == len(data) and ORG not in public and KEY not in public
        assert private == {ORG.decode(), KEY.decode()} and b'org-' + b'X' * 32 in public and b'<ak-' + b'X' * 20 + b'>' in public
        assert not sanitizer._ACCOUNT.search(public)
    # A word that ends in the same letters is not an id.
    assert sanitizer.alias_accounts(b'break-something1 and morgue-0123456789ab')[1] == 0


def test_plan_and_listing_digests_of_files_outside_the_packet_are_zeroed_at_equal_length():
    plan = sanitizer._compact({'attempt_id': 'kimi-test', 'kimi_executable_sha256': 'aa' * 32, 'safe_config_sha256': 'bb' * 32, 'source_config_sha256': 'cc' * 32,
                               'controller_source_sha256': 'dd' * 32})
    public, count = sanitizer.zero_plan_digests(plan)
    value = json.loads(public)
    assert count == 2 and len(public) == len(plan) and value['safe_config_sha256'] == value['source_config_sha256'] == '0' * 64
    assert value['kimi_executable_sha256'] == 'aa' * 32 and value['controller_source_sha256'] == 'dd' * 32
    receipt = sanitizer._compact({'exit_code': 0, 'stdout_sha256': 'ee' * 32, 'session_id': 'session_x',
                                  'kimi_home_inventory_after': {'config.toml': {'sha256': 'bb' * 32, 'size_bytes': 368}, 'device_id': {'sha256': 'ff' * 32, 'size_bytes': 36},
                                                                'session_index.jsonl': {'sha256': '11' * 32, 'size_bytes': 287}},
                                  'native_family_copied_inventory': {'state.json': {'sha256': '22' * 32, 'size_bytes': 462}, 'agents/main/wire.jsonl': {'sha256': '33' * 32, 'size_bytes': 9}}})
    public, count = sanitizer.zero_unbound_listing_digests(receipt, {'11' * 32, '22' * 32})
    value = json.loads(public)
    assert count == 3 and len(public) == len(receipt) and value['stdout_sha256'] == 'ee' * 32
    assert {name: row['sha256'][:2] for name, row in value['kimi_home_inventory_after'].items()} == {'config.toml': '00', 'device_id': '00', 'session_index.jsonl': '11'}
    assert value['native_family_copied_inventory']['state.json']['sha256'] == '22' * 32 and value['native_family_copied_inventory']['agents/main/wire.jsonl']['sha256'] == '0' * 64
    assert [row['size_bytes'] for row in value['kimi_home_inventory_after'].values()] == [368, 36, 287]
    with pytest.raises(ValueError, match='controller serialization'):
        sanitizer.zero_plan_digests(json.dumps(json.loads(plan)).encode())


def test_provider_message_id_passes_only_with_a_time_near_its_own_record():
    session = normal_session()
    wire = session.wire()
    name = 'run/native/session/agents/main/wire.jsonl'
    result = long_hex_runs({name: wire}, hex_allowlist=sanitizer.HEX_ALLOWLIST, public_references={})
    ids = set(re.findall(rb'chatcmpl-([0-9a-f]{24})', wire))
    assert len(ids) == 7 and result['classes']['allowlisted'] == 14 and not [row for row in result['unlisted'] if row['length'] == 24]
    # Another time part, or the same value in another field, is unlisted.
    forged = wire.replace(b'"messageId":"chatcmpl-6a', b'"messageId":"chatcmpl-5a')
    assert forged != wire and len([row for row in long_hex_runs({name: forged}, hex_allowlist=sanitizer.HEX_ALLOWLIST, public_references={})['unlisted']
                                   if row['length'] == 24]) == 14
    moved = wire.replace(b'"messageId":"chatcmpl-', b'"otherField":"chatcmpl-')
    assert len([row for row in long_hex_runs({name: moved}, hex_allowlist=sanitizer.HEX_ALLOWLIST, public_references={})['unlisted'] if row['length'] == 24]) == 14
    assert long_hex_runs({name: wire}, public_references={})['classes']['allowlisted'] == 0


# --- The real public set ---

@pytest.fixture(scope='module')
def candidates():
    if not (PUBLIC / 'summary.json').is_file():
        pytest.skip('Kimi public candidates are not in this checkout')
    return json.loads((PUBLIC / 'summary.json').read_bytes())


@real
def test_public_candidates_keep_the_31_metric_rows(candidates):
    assert [row['packet'] for row in candidates['runs']] == list(RUNS) and all(row['all_31_metric_rows_unchanged'] for row in candidates['runs'])
    for run in RUNS:
        private = json.loads((PRIVATE / f'{run}-receipt.json').read_bytes())['diagnostics']['intact']
        public = json.loads((PUBLIC / f'{run}-receipt.json').read_bytes())['diagnostics']['intact']
        assert private['metrics'] == public['metrics'] and len(public['metrics']) == 31
        # Duplicate safety and density evidence are equal too (record ids, roles and logical bytes; the proofs differ).
        for metric in ('broad.naive_reader_duplicate_safety', 'broad.classified_content_density'):
            assert private['format_evidence']['profile']['broad_evidence'][metric] == public['format_evidence']['profile']['broad_evidence'][metric]
        assert json.loads((PUBLIC / f'{run}-tamper.json').read_bytes())['status'] == 'passed'
    bundle = json.loads((PUBLIC / 'public-inputs-candidate.json').read_bytes())
    assert bundle['configuration_id'] == 'kimi' and len(bundle['pairs']) == 3
    assert sha((PUBLIC / 'public-inputs-candidate.json').read_bytes()) == candidates['public_bundle_sha256']
    for pair in bundle['pairs']:
        score_release_run(pair['survival'], pair['format'])


@real
def test_every_file_keeps_its_byte_length_and_the_wire_keeps_every_record(candidates):
    for run in RUNS:
        for path in sorted(item for folder in ('native', 'inputs') for item in (PRIVATE / run / folder).rglob('*') if item.is_file()):
            relative = path.relative_to(PRIVATE / run)
            assert (PUBLIC / run / relative).stat().st_size == path.stat().st_size, relative
        shape = lambda base: [(number, row.get('type'), sorted(row)) for number, row in read_wire((base / run / 'native' / SESSION_DIR / WIRE).read_bytes())]  # noqa: E731
        assert shape(PRIVATE) == shape(PUBLIC)
        # No stderr or native file of an unused attempt is in a packet: only the two turns of the run.
        turns = sorted(path.name for path in (PUBLIC / run / 'inputs/capture').iterdir() if path.is_dir())
        assert turns == ['turn-r1', 'turn-r2']
        artifacts = json.loads((PUBLIC / run / 'native/decode.json').read_bytes())['artifacts']
        assert read_kimi_family(PUBLIC / run / 'native', artifacts=artifacts)[2] == []


@real
def test_public_set_holds_no_private_value_no_vendor_text_and_only_bound_digests(candidates):
    public_set = read_public_set([PUBLIC / run for run in RUNS], [PUBLIC / 'public-inputs-candidate.json'])
    forbidden = set(sanitizer.encodings(Path.home().name))
    blanked = []
    for run in RUNS:
        for path in sorted(item for item in (PRIVATE / run).rglob('*') if item.is_file() and 'runtime' not in item.parts):
            forbidden |= {match.group(1) for match in sanitizer._ACCOUNT.finditer(path.read_bytes())}
        sanitizer.sanitize_wire((PRIVATE / run / 'native' / SESSION_DIR / WIRE).read_bytes(), blanked, set())
    assert len(forbidden) > 3 and len(blanked) > 600
    for name, data in public_set.items():
        assert not sanitizer.private_markers(data), name
        assert not [value for value in forbidden if value in data], name
        assert not sanitizer._ACCOUNT.search(data), name
    assert sanitizer.require_no_instruction_phrase(public_set, sanitizer.instruction_phrases(blanked)) > 500
    assert short_digest_warnings(public_set) == []
    receipts = [json.loads((PUBLIC / f'{run}-receipt.json').read_bytes()) for run in RUNS]
    result = check_public_packets([PUBLIC / run for run in RUNS], receipts=receipts, extra_files=[PUBLIC / 'public-inputs-candidate.json'],
                                  allowlist=sanitizer.DIGEST_ALLOWLIST, hex_allowlist=sanitizer.HEX_ALLOWLIST)
    assert result['unbound'] == [] and result['classes']['public_bytes'] > 0
    # Allowlisted: the digest of the vendor package file in each plan; the provider message ids (each stated twice).
    assert result['classes']['allowlisted'] == 3 and result['hex_runs']['unlisted'] == [] and result['hex_runs']['classes']['allowlisted'] == 40
    for run in RUNS:
        plan = json.loads((PUBLIC / run / 'inputs/capture/plan.json').read_bytes())
        assert plan['source_config_sha256'] == plan['safe_config_sha256'] == '0' * 64 and plan['source_config_path'].startswith('/Users/x')
        assert sha((PUBLIC / run / 'inputs/capture/controller-source.py').read_bytes()) == plan['controller_source_sha256']
