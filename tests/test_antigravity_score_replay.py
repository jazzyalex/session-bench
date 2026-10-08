"""Three whole-state Antigravity captures: the conversation database is the primary record."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import re

import pytest

from session_bench.antigravity_density import antigravity_step_role, read_antigravity_family
from session_bench.antigravity_format_evidence import antigravity_forward_occurrences
from session_bench.antigravity_live import decode_antigravity_family, stdout_projection
from session_bench.antigravity_score_inputs import apply_antigravity_native_absence
from session_bench.native_replay import _snapshot_tree, canonical
from session_bench.score_replay import replay_score_package, validate_score_packet

ROOT = Path(__file__).resolve().parents[1]
CAPTURES = ROOT / 'artifacts/v1-expanded-preparation/live-captures'
spec = importlib.util.spec_from_file_location('antigravity_score_builder', ROOT / 'scripts/build_antigravity_score_replays.py')
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)

SESSION = 'synthetic-session'
PRIMARY = SESSION + '/.system_generated/logs/transcript.jsonl'
WORKSPACE = '/work/fixture_project'
HEADER = 'Created At: 2026-10-04T13:00:00-07:00\nCompleted At: 2026-10-04T13:00:01-07:00\n'


def step(index, kind, **fields):
    source = {'USER_INPUT': 'USER_EXPLICIT', 'SYSTEM_MESSAGE': 'SYSTEM'}.get(kind, 'MODEL')
    return {'step_index': index, 'source': source, 'type': kind, 'status': 'DONE',
            'created_at': f'2026-10-04T20:00:{index:02d}Z', **fields}


def call(name, **args):
    return {'name': name, 'args': args}


def command_result(code, output):
    return HEADER + f'\nThe command exited with code {code}.\nOutput:\n{output}\n'


def full_read(text, path=WORKSPACE + '/checkout.py'):
    lines = text.split('\n')
    return (HEADER + f'File Path: `file://{path}`\nTotal Lines: {len(lines)}\nTotal Bytes: {len(text.encode())}\n'
            f'Showing lines 1 to {len(lines)}\nThe following code has line numbers.\n'
            + '\n'.join(f'{number}: {line}' for number, line in enumerate(lines, 1))
            + '\nThe above content shows the entire, complete file contents of the requested file.\n')


def write_family(root, rows, *, full_rows=None):
    """Write one session directory the way agy 1.2.16 does: two transcripts, their chunks and step outputs."""
    base = root / SESSION / '.system_generated'
    encoded = []
    for row in rows:
        row = json.loads(json.dumps(row))
        for item in row.get('tool_calls', []):
            item['args'] = {key: json.dumps(value) for key, value in item['args'].items()}
        encoded.append(row)
    short = ''.join(json.dumps(row) + '\n' for row in encoded).encode()
    full = ''.join(json.dumps(row) + '\n' for row in (full_rows or rows)).encode()
    for name, data in (('logs/transcript.jsonl', short), ('logs/transcript_full.jsonl', full),
                       ('logs/chunks/transcript/00000000.jsonl', short), ('logs/chunks/transcript_full/00000000.jsonl', full)):
        (base / name).parent.mkdir(parents=True, exist_ok=True)
        (base / name).write_bytes(data)
    for row in rows:
        if row['type'] == 'GENERIC' and 'File Path:' not in row['content']:
            target = base / 'steps' / str(row['step_index']) / 'output.txt'
            target.parent.mkdir(parents=True)
            target.write_text(row['content'].split('\n', 2)[2])
    return root


def artifacts(root):
    return [{'path': path.relative_to(root).as_posix(), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
             'size_bytes': path.stat().st_size} for path in sorted(root.rglob('*')) if path.is_file()]


ROWS = [
    step(0, 'USER_INPUT', content='<USER_REQUEST>\nR1 task\n</USER_REQUEST>\n<ADDITIONAL_METADATA>\nclock\n</ADDITIONAL_METADATA>'),
    step(1, 'PLANNER_RESPONSE', input_tokens=10, cache_read_tokens=0, output_tokens=5,
         tool_calls=[call('run_command', CommandLine='python3 bench_check.py inspect --run-canary SB_SURVIVAL_V1_RUN_x', Cwd=WORKSPACE)]),
    step(2, 'GENERIC', content=command_result(0, 'SB_SURVIVAL_V1_HELPER_INSPECT_inspect-fixture-0001 {}\r\n')),
    step(3, 'PLANNER_RESPONSE', input_tokens=3, cache_read_tokens=9, output_tokens=7, content='Seen.\n\nSB_SURVIVAL_V1_RESPONSE_R1_x'),
    step(4, 'USER_INPUT', content='<USER_REQUEST>\nCorrection R2 task\n</USER_REQUEST>'),
    step(5, 'PLANNER_RESPONSE', input_tokens=3, cache_read_tokens=9, output_tokens=7, content='I will read the file first.',
         tool_calls=[call('view_file', AbsolutePath=WORKSPACE + '/checkout.py')]),
    step(6, 'GENERIC', content=full_read('one\ntwo\n')),
    step(7, 'PLANNER_RESPONSE', input_tokens=3, cache_read_tokens=9, output_tokens=7,
         tool_calls=[call('write_to_file', TargetFile=WORKSPACE + '/checkout.py', CodeContent='one\nthree\n', Overwrite=True)]),
    step(8, 'GENERIC', content=HEADER + f'Created file file://{WORKSPACE}/checkout.py with requested content.\nIf relevant, run it.'),
    step(9, 'PLANNER_RESPONSE', input_tokens=3, cache_read_tokens=9, output_tokens=7, content='Done.\n\nSB_SURVIVAL_V1_RESPONSE_R2_x'),
]


def test_stdout_projection_maps_a_whole_file_write_to_an_edit_and_keeps_step_text():
    rows = [{'event': 'init', 'conversation_id': 's'},
            {'event': 'step_update', 'step_update': {'conversation_id': 's', 'step_index': 3, 'state': 'DONE', 'step_type': 'tool',
             'tool_info': {'name': 'write_to_file', 'parameters': {'TargetFile': '/w/fixture_project/checkout.py'}}}},
            {'event': 'step_update', 'step_update': {'conversation_id': 's', 'step_index': 4, 'state': 'ACTIVE', 'step_type': 'agent_response', 'text_delta': 'note\n'}},
            {'event': 'step_update', 'step_update': {'conversation_id': 's', 'step_index': 4, 'state': 'DONE', 'step_type': 'agent_response'}},
            {'event': 'step_update', 'step_update': {'conversation_id': 's', 'step_index': 6, 'state': 'ACTIVE', 'step_type': 'agent_response', 'text_delta': 'done '}},
            {'event': 'step_update', 'step_update': {'conversation_id': 's', 'step_index': 6, 'state': 'DONE', 'step_type': 'agent_response', 'text_delta': 'CANARY\n'}},
            {'event': 'result', 'result': {'conversation_id': 's', 'status': 'SUCCESS', 'response': 'note\ndone CANARY\n'}}]
    raw = '\n'.join(map(json.dumps, rows))
    legacy = [json.loads(line) for line in stdout_projection(raw, [])['jsonl'].splitlines()]
    assert [row['type'] for row in legacy] == ['tool_use', 'text'] and legacy[1]['text'] == 'note\ndone CANARY\n'
    assert legacy[0]['tool'] == 'edit' and legacy[0]['input'] == {'filePath': '/w/fixture_project/checkout.py'}
    # The stream ends each text step with one line feed. The per-step view removes exactly that one.
    stepped = [json.loads(line) for line in stdout_projection(raw, [], step_text=True)['jsonl'].splitlines()]
    assert [(row['type'], row.get('text')) for row in stepped] == [('tool_use', None), ('text', 'note'), ('text', 'done CANARY')]
    broken = raw.replace('note\\ndone CANARY\\n', 'other text\\n')
    with pytest.raises(ValueError, match='step text'):
        stdout_projection(broken, [], step_text=True)


def test_family_decoder_keeps_native_facts_and_invents_no_action_result_join(tmp_path):
    root = write_family(tmp_path / 'native', ROWS)
    decoded = decode_antigravity_family(root, PRIMARY)
    assert decoded['status'] == 'ok' and decoded['diagnostics'] == [] and decoded['session_id'] == SESSION
    assert [turn['text'] for turn in decoded['turns']] == ['R1 task', 'Correction R2 task']
    # A planner step that also requests a tool is commentary, not the final response of the turn.
    assert [(row['id'], row['phase']) for row in decoded['responses']] == [
        ('step:3', 'final_answer'), ('step:5', 'commentary'), ('step:9', 'final_answer')]
    assert [row['kind'] for row in decoded['relations']] == ['turn_response', 'turn_response']
    assert [(row['name'], row.get('target')) for row in decoded['actions']] == [
        ('bash', 'fixture_project/checkout.py'), ('view_file', 'fixture_project/checkout.py'), ('edit', 'fixture_project/checkout.py')]
    results = {row['id']: row for row in decoded['results']}
    assert all('call_id' not in row and 'action_id' not in row for row in results.values())
    assert results['step:2']['output'] == 'SB_SURVIVAL_V1_HELPER_INSPECT_inspect-fixture-0001 {}\r\n'
    assert (results['step:2']['exit_code'], results['step:2']['helper_nonce']) == (0, 'inspect-fixture-0001')
    assert (results['step:8']['status'], results['step:8']['exit_code'], results['step:8']['target']) == ('success', 0, 'fixture_project/checkout.py')
    # Native usage has no cache-write count: it stays a fragment with the native names.
    assert decoded['responses'][0]['usage'] == {'input_tokens': 3, 'cache_read_tokens': 9, 'output_tokens': 7}
    assert decoded['usage'] == [] and all('model' not in row and 'model_id' not in row for row in decoded['responses'])
    change, = decoded['file_changes']
    assert change['before_sha256'] == hashlib.sha256(b'one\ntwo\n').hexdigest()
    assert change['after_sha256'] == hashlib.sha256(b'one\nthree\n').hexdigest()
    assert change['hash_source'] == 'native_preimage_and_whole_file_write' and change['locator']['step_index'] == 8
    assert all(row['timestamp'] == row['raw']['created_at'] for row in decoded['records'])


@pytest.mark.parametrize('damage', ['no_read', 'no_completion', 'second_write', 'not_overwrite'])
def test_whole_file_write_gives_hashes_only_with_a_complete_read_one_call_and_its_completion(tmp_path, damage):
    rows = json.loads(json.dumps(ROWS))
    if damage == 'no_read':
        rows[6]['content'] = HEADER + 'File Path: `file:///other`\nTotal Lines: 1\n'
    elif damage == 'no_completion':
        rows[8]['content'] = HEADER + 'The write failed.'
    elif damage == 'second_write':
        rows[7]['tool_calls'].append(call('write_to_file', TargetFile=WORKSPACE + '/checkout.py', CodeContent='x\n', Overwrite=True))
    else:
        rows[7]['tool_calls'][0]['args']['Overwrite'] = False
    decoded = decode_antigravity_family(write_family(tmp_path / 'native', rows), PRIMARY)
    assert decoded['file_changes'] == []


def test_family_copies_that_differ_and_unknown_records_are_contract_exceptions(tmp_path):
    root = write_family(tmp_path / 'a', ROWS)
    records, _, exceptions = read_antigravity_family(root, artifacts=artifacts(root), primary=PRIMARY)
    assert exceptions == []
    kinds = {row['record_id']: row['record_kind'] for row in records}
    assert kinds[PRIMARY + ':line-1'] == 'user_message' and kinds[PRIMARY + ':line-2'] == 'tool_call'
    assert kinds[PRIMARY + ':line-3'] == 'tool_result' and kinds[PRIMARY + ':line-4'] == 'assistant_message'
    assert kinds[SESSION + '/.system_generated/steps/2/output.txt'] == 'tool_result'
    assert antigravity_step_role(step(1, 'FUTURE_STEP', content='x')) == 'unknown'
    changed = json.loads(json.dumps(ROWS))
    changed[3]['content'] = 'another answer'
    root = write_family(tmp_path / 'b', ROWS, full_rows=changed)
    assert [row['code'] for row in read_antigravity_family(root, artifacts=artifacts(root), primary=PRIMARY)[2]] == ['full_transcript_differs']
    root = write_family(tmp_path / 'c', ROWS)
    (root / SESSION / '.system_generated/logs/chunks/transcript/00000000.jsonl').write_bytes(b'{}\n')
    codes = [row['code'] for row in read_antigravity_family(root, artifacts=artifacts(root), primary=PRIMARY)[2]]
    assert 'chunk_copy_differs' in codes
    root = write_family(tmp_path / 'd', ROWS + [step(10, 'FUTURE_STEP', content='x')])
    decoded = decode_antigravity_family(root, PRIMARY)
    assert decoded['status'] == 'unsupported' and decoded['diagnostics'][0]['code'] == 'unknown_step_type'


def test_every_event_has_one_active_occurrence_per_transcript_copy(tmp_path):
    root = write_family(tmp_path / 'native', ROWS)
    occurrences = antigravity_forward_occurrences(root, artifacts=artifacts(root), primary=PRIMARY)
    by_event = {}
    for event, occurrence in occurrences:
        by_event.setdefault(event, []).append(occurrence)
    # Two transcripts and two chunk copies. A step output file repeats its result once more.
    assert len(by_event['message:step:0']) == 4 and len(by_event['call:step:1:0']) == 4
    assert len(by_event['result:step:2']) == 5 and len(by_event['result:step:6']) == 4
    assert len({occurrence for values in by_event.values() for occurrence in values}) == len(occurrences)


def test_model_absence_is_resolved_only_with_a_complete_cleanly_decoded_root():
    decoded = {'status': 'ok', 'diagnostics': []}
    measurement = {'metrics': [
        {'id': 'attribution.usage', 'state': 'unresolved', 'correct': 0, 'observed_eligible': 2, 'decoded_eligible': 2},
        {'id': 'attribution.model_config', 'state': 'unresolved', 'correct': 0, 'observed_eligible': 2, 'decoded_eligible': 2}]}
    states = lambda value: [row['state'] for row in value['metrics']]
    # Usage follows the shared comparator rule: there is no Antigravity override for it.
    assert states(apply_antigravity_native_absence(measurement, decoded, complete_root=True)) == ['unresolved', 'native_absent']
    assert states(apply_antigravity_native_absence(measurement, decoded, complete_root=False)) == ['unresolved'] * 2
    carried = {**decoded, 'responses': [{'phase': 'final_answer', 'model_id': 'm', 'configuration': 'c'}]}
    assert states(apply_antigravity_native_absence(measurement, carried, complete_root=True)) == ['unresolved'] * 2


# --- Shared comparator: usage needs input and output; token semantics needs all four counts ---
def _observer(run='r'):
    event = lambda ident, kind, fields, seq: {'id': ident, 'sequence': seq, 'population_role': 'primary_scored', 'kind': kind,
                                             'session_id': 's', 'fields': fields, 'metric_ids': []}
    events = [event('turn-r1', 'user_turn', {'revision': 'r1', 'role': 'user', 'text': 'R1 task', 'run_canary': 'X'}, 1),
              event('response-r1', 'assistant_response', {'turn_id': 'turn-r1', 'role': 'assistant', 'status': 'completed', 'canary': 'SB_SURVIVAL_V1_RESPONSE_R1_x'}, 2),
              event('turn-r2', 'user_turn', {'revision': 'r2', 'role': 'user', 'text': 'Correction R2 task', 'run_canary': 'X'}, 3),
              event('response-r2', 'assistant_response', {'turn_id': 'turn-r2', 'role': 'assistant', 'status': 'completed', 'canary': 'SB_SURVIVAL_V1_RESPONSE_R2_x'}, 4)]
    return {'schema_version': '1.0-survival-observer', 'protocol_version': '1.0-survival', 'scenario_id': 'survival-v1-repair',
            'run_id': run, 'independent': True, 'events': events,
            'relations': [{'id': 'order', 'kind': 'supersedes', 'from_id': 'turn-r1', 'to_id': 'turn-r2', 'sequence': 1}]}


def _native(usage):
    turns = [{'id': 't1', 'role': 'user', 'text': 'R1 task', 'sequence': 1}, {'id': 't2', 'role': 'user', 'text': 'Correction R2 task', 'sequence': 3}]
    responses = [{'id': f'a{n}', 'role': 'assistant', 'turn_id': f't{n}', 'status': 'completed', 'text': f'x SB_SURVIVAL_V1_RESPONSE_R{n}_x',
                  'sequence': 2 * n, **({'usage': dict(usage)} if usage else {})} for n in (1, 2)]
    return {'status': 'ok', 'diagnostics': [], 'turns': turns, 'responses': responses, 'actions': [], 'results': [], 'file_changes': [], 'relations': [], 'usage': []}


@pytest.mark.parametrize('usage, complete, expected_usage, expected_semantics', [
    ({'input_tokens': 5, 'output_tokens': 7, 'cache_read_tokens': 0, 'cache_write_tokens': 0}, True, ('measured', 2, 2), ('measured', 2, 2)),
    # Three counts: the usage join holds. A missing cache-write key is not a zero.
    ({'input_tokens': 5, 'output_tokens': 7, 'cache_read_tokens': 9}, True, ('measured', 2, 2), ('native_absent', 0, 2)),
    ({'input_tokens': 5, 'output_tokens': 7, 'cache_read_tokens': 9}, False, ('measured', 2, 2), ('unresolved', 0, 2)),
    ({'input_tokens': 5, 'output_tokens': 7}, True, ('measured', 2, 2), ('native_absent', 0, 2)),
    ({'input_tokens': 5, 'cache_read_tokens': 1, 'cache_write_tokens': 1}, False, ('unresolved', 0, 2), ('unresolved', 0, 0)),
    ({'input_tokens': 5, 'output_tokens': None, 'cache_read_tokens': 1, 'cache_write_tokens': 1}, False, ('unresolved', 0, 2), ('unresolved', 0, 0)),
])
def test_usage_needs_input_and_output_and_token_semantics_needs_four_counts(usage, complete, expected_usage, expected_semantics):
    from session_bench.live_metric_comparator import compare_survival_run
    portable = {'complete_root': complete, 'companions_present': True, 'isolated_decode': True, 'canonical_equality': True}
    rows = {row['id']: row for row in compare_survival_run(_observer(), _native(usage), portable, configuration_id='antigravity', repetition=1)['metrics']}
    view = lambda name: (rows[name]['state'], rows[name]['correct'], rows[name]['decoded_eligible'])
    assert view('attribution.usage') == expected_usage and view('attribution.token_semantics') == expected_semantics


# --- Whole-state family: the conversation database is the primary record ---
from session_bench.antigravity_conversation_db import encode, pack_time, read_conversation_db, remove_step  # noqa: E402
from session_bench.antigravity_live import decode_antigravity_conversation  # noqa: E402
from session_bench.antigravity_state_inputs import paired_action_pool  # noqa: E402
import sqlite3  # noqa: E402

DB_SESSION = '11111111-2222-3333-4444-555555555555'


def write_db(path, steps, *, user_version=1, generations=()):
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.executescript("""
        CREATE TABLE trajectory_meta (trajectory_id text, cascade_id text, trajectory_type integer, source integer, PRIMARY KEY (trajectory_id));
        CREATE TABLE steps (idx integer, step_type integer NOT NULL DEFAULT 0, status integer NOT NULL DEFAULT 0, has_subtrajectory numeric NOT NULL DEFAULT false,
            metadata blob, error_details blob, permissions blob, task_details blob, render_info blob, step_payload blob, step_format integer NOT NULL DEFAULT 0, PRIMARY KEY (idx));
        CREATE TABLE gen_metadata (idx integer, data blob, size integer NOT NULL DEFAULT 0, PRIMARY KEY (idx));
        CREATE TABLE executor_metadata (idx integer, data blob, PRIMARY KEY (idx));
        CREATE TABLE parent_references (idx integer, data blob, PRIMARY KEY (idx));
        CREATE TABLE trajectory_metadata_blob (id text DEFAULT 'main', data blob, PRIMARY KEY (id));
        CREATE TABLE battle_mode_infos (idx integer, data blob, PRIMARY KEY (idx));
    """)
    connection.execute(f'PRAGMA user_version={user_version}')
    connection.execute('INSERT INTO trajectory_meta VALUES (?,?,4,17)', ('trajectory', DB_SESSION))
    for index, (kind, metadata, payload) in enumerate(steps):
        connection.execute('INSERT INTO steps (idx,step_type,status,metadata,step_payload) VALUES (?,?,3,?,?)',
                           (index, kind, encode([(1, pack_time(1791224000 + index, 5))] + metadata), encode(payload)))
    for index, data in enumerate(generations):
        connection.execute('INSERT INTO gen_metadata (idx,data,size) VALUES (?,?,?)', (index, encode(data), len(encode(data))))
    connection.commit(); connection.close()
    return path


def model_step(response, *, text=None, calls=(), usage=(5, 7, 9)):
    metadata = [(9, [(1, 1035), (2, usage[0]), (3, usage[1]), (5, usage[2]), (7, response)])]
    payload = ([(1, text)] if text else []) + [(6, response)] + [(7, [(1, ident), (2, name), (3, json.dumps(args))]) for ident, name, args in calls]
    return 15, metadata, [(20, payload)]


def tool_step(call, name, args, body):
    return 132, [(4, [(1, call), (2, name), (3, json.dumps(args))])], [(140, [(2, [(1, body)])])]


def generation(response, messages=()):
    return [(1, [(4, [(7, response)]), (19, 'model-x'), (20, [(1, 'model_enum'), (2, 'MODEL_X')])]
                 + [(2, message) for message in messages] + [(16, [(1, 'identity'), (2, 'You are Antigravity, an assistant.')])])]


INSPECT = {'CommandLine': 'python3 bench_check.py inspect --run-canary SB_SURVIVAL_V1_RUN_x', 'Cwd': WORKSPACE}
DB_STEPS = [
    (14, [], [(19, [(2, 'R1 task')])]),
    model_step('bot-1', calls=[('call-ls', 'run_command', {'CommandLine': 'ls', 'Cwd': WORKSPACE})]),
    tool_step('call-ls', 'run_command', {'CommandLine': 'ls'}, '\nThe command exited with code 0.\nOutput:\ncheckout.py\r\n\n'),
    model_step('bot-2', calls=[('call-inspect', 'run_command', INSPECT)]),
    tool_step('call-inspect', 'run_command', INSPECT, '\nThe command exited with code 0.\nOutput:\nSB_SURVIVAL_V1_HELPER_INSPECT_inspect-fixture-0001 {}\r\n\n'),
    model_step('bot-3', text='Seen.\n\nSB_SURVIVAL_V1_RESPONSE_R1_x'),
    (14, [], [(19, [(2, 'Correction R2 task')])]),
    (101, [], [(114, [(4, [(4, '[Notice] restart')])])]),
    model_step('bot-4', text='Done.\n\nSB_SURVIVAL_V1_RESPONSE_R2_x'),
]


def test_conversation_database_gives_call_result_keys_usage_and_model(tmp_path):
    root = tmp_path / 'native'
    write_db(root / f'conversations/{DB_SESSION}.db', DB_STEPS, generations=[generation('bot-3'), generation('bot-4')])
    decoded = decode_antigravity_conversation(root, DB_SESSION)
    assert decoded['status'] == 'ok' and decoded['diagnostics'] == [] and decoded['user_version'] == 1
    assert [turn['text'] for turn in decoded['turns']] == ['R1 task', 'Correction R2 task']
    # The relation comes from the native call id, never from the order of steps.
    relations = [(row['from_id'], row['to_id'], row['call_id']) for row in decoded['relations'] if row['kind'] == 'action_result']
    assert relations == [('step:1:call:0', 'step:2', 'call-ls'), ('step:3:call:0', 'step:4', 'call-inspect')]
    response = decoded['responses'][0]
    assert response['usage'] == {'input_tokens': 5, 'output_tokens': 7, 'cache_read_tokens': 9} and 'cache_write_tokens' not in response['usage']
    assert (response['model_id'], response['configuration']) == ('model-x', 'MODEL_X')
    assert decoded['results'][1]['helper_nonce'] == 'inspect-fixture-0001' and decoded['results'][1]['exit_code'] == 0
    assert all(record['timestamp'] == 1791224000 + index for index, record in enumerate(decoded['records']))
    # A second call with the same id, or a tool step without a call, gives no relation.
    doubled = list(DB_STEPS)
    doubled[3] = model_step('bot-2', calls=[('call-ls', 'run_command', INSPECT)])
    write_db(tmp_path / 'b' / f'conversations/{DB_SESSION}.db', doubled)
    again = decode_antigravity_conversation(tmp_path / 'b', DB_SESSION)
    assert not [row for row in again['relations'] if row['kind'] == 'action_result' and row['to_id'] == 'step:4']


def test_conversation_database_contract_refuses_another_version_step_type_or_conversation(tmp_path):
    for name, change in (('version', {'user_version': 2}), ('type', {}), ('session', {})):
        steps = DB_STEPS + [(77, [], [])] if name == 'type' else DB_STEPS
        write_db(tmp_path / name / f'conversations/{DB_SESSION}.db', steps, **change)
    codes = lambda name, session=DB_SESSION: [row['code'] for row in decode_antigravity_conversation(tmp_path / name, session, store=read_conversation_db(
        (tmp_path / name / f'conversations/{DB_SESSION}.db').read_bytes()))['diagnostics']]
    assert codes('version') == ['unsupported_user_version'] and codes('type') == ['unknown_step_type']
    assert codes('session', 'another-conversation-id') == ['conversation_id_differs']
    database = (tmp_path / 'version' / f'conversations/{DB_SESSION}.db').read_bytes()
    assert len(read_conversation_db(remove_step(database, 8))['tables']['steps']) == len(DB_STEPS) - 1


def test_statements_inside_the_database_are_counted_and_mirrors_are_proved_by_content(tmp_path):
    from session_bench.antigravity_density import mirror_differences, read_state_family, state_identity
    from session_bench.antigravity_format_evidence import state_forward_occurrences
    # The copy of the messages sent to the model: thinking text sits in field 11, and the system notice is copied too.
    snapshot = [[(18, 0), (2, 1), (3, 'R1 task')], [(18, 3), (2, 2), (11, 'think first'), (6, [(1, 'call-inspect'), (2, 'run_command')])],
                [(18, 4), (2, 4), (3, 'out')], [(18, 7), (2, 1), (3, '[Notice] restart')]]
    root = tmp_path / 'native'
    thinking = list(DB_STEPS)
    kind, metadata, payload = thinking[3]
    thinking[3] = (kind, metadata, [(20, [(3, 'think first'), *payload[0][1]])])
    database = write_db(root / f'conversations/{DB_SESSION}.db', thinking, generations=[generation('bot-3', snapshot), generation('bot-4')])
    store = read_conversation_db(database.read_bytes())
    by_event = {}
    for event, occurrence in state_forward_occurrences(store):
        by_event.setdefault(event, []).append(occurrence)
    # Call: model step, tool step copy, prompt copy. Result: tool step, prompt copy. Final response: its step only.
    assert len(by_event['call:call-inspect']) == 3 and len(by_event['result:step:4']) == 2 and len(by_event['message:step:0']) == 2
    assert by_event['message:step:8'] == ['steps:8:message'] and len(by_event['call:call-ls']) == 2
    # A model step that holds only thinking text is stated again by field 11 of the copy.
    assert by_event['message:step:3'] == ['steps:3:message', 'gen_metadata:0:message-1:text']
    # The system notice is injected context that step_type 101 marks: it is no event, in its row or in the copy.
    assert 'message:step:7' not in by_event and not [name for name in by_event if name.endswith(':7')]
    assert state_identity(store, DB_SESSION) == {'session_id': DB_SESSION, 'harness': 'Antigravity'}
    # A transcript that states the same steps is a mirror; one changed text is found by comparison.
    rows = [step(0, 'USER_INPUT', content='<USER_REQUEST>\nR1 task\n</USER_REQUEST>'),
            step(1, 'PLANNER_RESPONSE', tool_calls=[call('run_command', CommandLine='ls', Cwd=WORKSPACE)]),
            step(2, 'GENERIC', content=HEADER + '\nThe command exited with code 0.\nOutput:\ncheckout.py\r\n\n'),
            step(3, 'PLANNER_RESPONSE', tool_calls=[call('run_command', **INSPECT)]),
            step(4, 'GENERIC', content=command_result(0, 'SB_SURVIVAL_V1_HELPER_INSPECT_inspect-fixture-0001 {}\r\n')),
            step(5, 'PLANNER_RESPONSE', content='Seen.\n\nSB_SURVIVAL_V1_RESPONSE_R1_x'),
            step(6, 'USER_INPUT', content='<USER_REQUEST>\nCorrection R2 task\n</USER_REQUEST>'),
            step(7, 'SYSTEM_MESSAGE', content='<SYSTEM_MESSAGE>\n[Notice] restart\n</SYSTEM_MESSAGE>'),
            step(8, 'PLANNER_RESPONSE', content='Done.\n\nSB_SURVIVAL_V1_RESPONSE_R2_x')]
    encoded = lambda values: ''.join(json.dumps({**row, **({'tool_calls': [{'name': c['name'], 'args': {k: json.dumps(v) for k, v in c['args'].items()}} for c in row['tool_calls']]} if 'tool_calls' in row else {})}) + '\n' for row in values).encode()
    assert mirror_differences(store, encoded(rows)) == []
    changed = json.loads(json.dumps(rows)); changed[5]['content'] = 'Another answer'
    assert mirror_differences(store, encoded(changed)) == [5]
    assert mirror_differences(store, encoded(rows[:-1])) == ['step list']
    # Family roles: the transcript mirror is a snapshot, the database rows keep their roles.
    global SESSION
    brain = write_family(tmp_path / 'brainsrc', rows)
    (root / 'brain').mkdir()
    (brain / SESSION).rename(root / 'brain' / DB_SESSION)
    (root / 'annotations').mkdir(); (root / f'annotations/{DB_SESSION}.pbtxt').write_text('title:"Fix"')
    records, _, exceptions = read_state_family(root, artifacts=artifacts(root), session_id=DB_SESSION, store=store)
    kinds = {}
    for record in records:
        kinds.setdefault(record['record_kind'], []).append(record['record_id'])
    assert exceptions == [] and all(name.startswith(f'conversations/{DB_SESSION}.db:steps:') for name in kinds['tool_result'] + kinds['user_message'])
    assert all(name.startswith('brain/') or ':gen_metadata:' in name for name in kinds['snapshot']) and len(kinds['snapshot']) == 4 * 9 + 2 + 1
    assert kinds['index'] == [f'annotations/{DB_SESSION}.pbtxt']


def test_unscored_native_actions_leave_the_pool_and_only_calls_above_the_observed_count_stay():
    def native(ident, command):
        return {'id': ident, 'name': 'bash', 'tool_name': 'bash', 'turn_id': 't1', 'input': {'CommandLine': command}, 'command': command,
                'argv': command.split(), 'target': 'fixture_project/checkout.py' if 'bench_check' in command else None}
    def seen(ident, command, role):
        return {'id': ident, 'kind': 'action', 'population_role': role, 'sequence': 1, 'session_id': 's', 'metric_ids': [],
                'fields': {'action_kind': 'inspect' if 'inspect' in command else 'bash', 'name': 'bash', 'turn_id': 'turn-r1', 'argv': command.split(),
                           **({'target': 'fixture_project/checkout.py'} if 'bench_check' in command else {})}}
    inspect = 'python3 bench_check.py inspect --run-canary SB_SURVIVAL_V1_RUN_x'
    observer = _observer()
    observer['events'] += [seen('a1', 'ls', 'unscored'), seen('a2', inspect, 'unscored'), seen('a3', inspect, 'primary_scored')]
    decoded = _native(None)
    pool = lambda *calls: [row['id'] for row in paired_action_pool({**decoded, 'actions': list(calls)}, observer)]
    # ls pairs with the unscored read; two inspect calls pair with one unscored and one scored action.
    assert pool(native('n1', 'ls'), native('n2', inspect), native('n3', inspect)) == ['n2']
    # A third equal inspect call is above the observed count: it stays as a duplicate.
    assert pool(native('n2', inspect), native('n3', inspect), native('n4', inspect)) == ['n2', 'n3']
    # A call that the observer did not see at all stays a dangling candidate.
    assert pool(native('n5', 'rm -rf build'), native('n2', inspect)) == ['n5', 'n2']


# --- Shared-store rows of the conversation: read after the capture, bound in the packet ---
def summary_message(steps=9, extra=()):
    stamp = [(1, 1791224000), (2, 5)]
    workspace = [(1, 'file:///work'), (2, 'file:///repo'), (3, [(1, 'octocat/project'), (2, 'https://example.test/octocat/project.git')]), (4, 'main')]
    return [(1, 'Fix'), (2, steps), (3, stamp), (4, 'trajectory'), (5, 1), (7, stamp), (9, workspace), (10, stamp), (15, [(1, 'Fix')]), (16, 6),
            (17, [(1, workspace), (2, stamp), (3, 'workspace-id'), (6, DB_SESSION), (7, 'file:///work'), (18, 'project-id')]), (22, 4), *extra]


def extract_document(summary=None, *, row=None, entries=None, session=DB_SESSION):
    summary = summary_message() if summary is None else summary
    columns = {'conversation_id': session, 'title': 'Fix', 'preview': 'Fix', 'step_count': 9, 'last_modified_time': 't', 'workspace_uris': '[]',
               'status': 'CASCADE_RUN_STATUS_IDLE', 'source': '', 'project_id': 'project-id', 'agent_name': '', 'parent_conversation_id': '',
               'nesting_depth': 0, 'battle_id': '', 'winning_conversation_id': '', 'not_fully_idle': 0, 'killed': 0, 'last_user_input_time': 't',
               'last_user_input_step_index': 6, 'app_data_dir': 'antigravity-cli', 'raw_summary': {'hex': encode(summary).hex()}, 'group_id': '', **(row or {})}
    return canonical({'schema_version': 'session-bench-antigravity-shared-store-extract-v1', 'conversation_id': session, 'read_on': '2026-10-05',
                      'authorization': 'owner', 'note': 'read after the capture',
                      'conversation_summaries_db': {'table': 'conversation_summaries', 'user_version': 3, 'row': columns},
                      'jetbox_summaries_proto_pb': {'entries_with_this_id': [encode([(1, session), (2, summary)]).hex()] if entries is None else entries}})


def test_shared_store_rows_prove_absence_only_when_every_field_is_a_known_summary_field(tmp_path):
    from session_bench.antigravity_state_inputs import apply_shared_store_absence, shared_store_findings
    store = read_conversation_db(write_db(tmp_path / 'c.db', DB_STEPS).read_bytes())
    good = shared_store_findings(extract_document(), store, DB_SESSION)
    assert good['proved'] is True and good['reasons'] == []
    assert ('raw_summary', '2', 'varint') in [tuple(row) for row in good['fields']] and ('jetbox_entry', '2.17.18', 'bytes') in [tuple(row) for row in good['fields']]
    reasons = lambda data: shared_store_findings(data, store, DB_SESSION)['reasons']
    # A field the map does not name could be a count. Then nothing is proved.
    assert reasons(extract_document(summary_message(extra=[(30, 12345)]))) == ['unknown field raw_summary:30', 'unknown field jetbox_entry:2.30']
    assert reasons(extract_document(row={'total_tokens': 5})) == ['unknown column total_tokens']
    assert reasons(extract_document(summary_message(steps=8))) == ['summary does not describe the captured conversation']
    assert reasons(extract_document(session='another-conversation'))[0] == 'extract names another conversation'
    assert reasons(extract_document(entries=[])) == ['jetbox entry count is not one']
    assert reasons(None) == ['shared-store extract is missing']
    measurement = {'metrics': [
        {'id': 'attribution.usage', 'state': 'measured', 'correct': 2, 'observed_eligible': 2, 'decoded_eligible': 2},
        {'id': 'attribution.token_semantics', 'state': 'unresolved', 'correct': 0, 'observed_eligible': 2, 'decoded_eligible': 2},
        {'id': 'attribution.reconciliation', 'state': 'unresolved', 'correct': 0, 'observed_eligible': 1, 'decoded_eligible': 0},
        {'id': 'work.results', 'state': 'unresolved', 'correct': 0, 'observed_eligible': 4, 'decoded_eligible': 4}]}
    decoded = {'status': 'ok', 'diagnostics': []}
    states = lambda findings, value=decoded: [row['state'] for row in apply_shared_store_absence(measurement, value, findings)['metrics']]
    assert states(good) == ['measured', 'native_absent', 'native_absent', 'unresolved']
    assert states({'proved': False, 'reasons': ['x'], 'fields': []}) == ['measured', 'unresolved', 'unresolved', 'unresolved']
    assert states(good, {'status': 'unsupported', 'diagnostics': []}) == ['measured', 'unresolved', 'unresolved', 'unresolved']
    # The usage record stays in the decoded population.
    assert apply_shared_store_absence(measurement, decoded, good)['metrics'][1]['decoded_eligible'] == 2


@pytest.fixture(scope='module')
def packets(tmp_path_factory):
    output = tmp_path_factory.mktemp('antigravity') / 'packets'
    return output, builder.build(output)


def test_designated_runs_are_the_first_three_whole_state_captures():
    assert builder.RUNS == ('antigravity-2026-10-05-02', 'antigravity-2026-10-05-03', 'antigravity-2026-10-05-04')


def test_whole_state_packets_resolve_all_31_metrics(packets):
    output, summary = packets
    assert [row['resolved_metric_count'] for row in summary['runs']] == [31, 31, 31]
    assert all(row['os_sandboxed'] and row['tamper_controls'] == 'passed' and row['selected_loss_detected'] for row in summary['runs'])
    for row in summary['runs']:
        receipt = json.loads((output / f'{row["packet"]}-receipt.json').read_bytes())
        metrics = {item['id']: item for item in receipt['diagnostics']['intact']['metrics']}
        state = lambda name: (metrics[name]['state'], metrics[name]['correct'], metrics[name]['observed_eligible'], metrics[name]['decoded_eligible'])
        # The database holds the call id on both ends.
        assert state('causal.action_result') == ('measured', 4, 4, 4) and state('work.results') == ('measured', 4, 4, 4)
        # Exploration calls pair with unscored observed actions and leave the population.
        assert state('work.actions') == ('measured', 4, 4, 4) and state('broad.event_timestamps') == ('measured', 13, 13, 13)
        assert state('work.changed_files') == ('measured', 1, 1, 1) and state('revision.final_after_r2') == ('measured', 1, 1, 1)
        assert state('attribution.model_config') == ('measured', 2, 2, 2) and state('attribution.usage') == ('measured', 2, 2, 2)
        # No cache-write key and no total: not in the family and not in the shared-store rows of the conversation.
        assert state('attribution.token_semantics') == ('native_absent', 0, 2, 2)
        assert state('attribution.reconciliation') == ('native_absent', 0, 1, 0)
        damaged = {item['id']: item['state'] for item in receipt['diagnostics']['selected_loss']['damaged']['metrics']}
        # With a step row removed the summary no longer describes the database, so nothing is proved.
        assert damaged['attribution.reconciliation'] == 'unresolved'
        assert state('portable.complete_root') == ('contradiction', 0, 1, 1)
        for name in ('portable.companions', 'portable.isolated_decode', 'portable.canonical_equality', 'broad.thread_structure',
                     'broad.documented_format', 'broad.self_contained_identity', 'broad.declared_format_version',
                     'broad.honest_version_signal', 'broad.observed_schema_stability', 'broad.stable_root_location'):
            assert state(name) == ('measured', 1, 1, 1), name
        assert state('broad.standard_tools_readable') == ('native_absent', 0, 1, 1)
        assert state('broad.readable_rationale') == ('measured', 2, 2, 2)
        duplicate = metrics['broad.naive_reader_duplicate_safety']
        # Every event is stated at least twice inside the database: nothing passes.
        assert duplicate['state'] == 'native_absent' and duplicate['correct'] == 0 and duplicate['decoded_eligible'] > 2 * duplicate['observed_eligible']
        density = metrics['broad.classified_content_density']
        assert 0 < density['correct'] < density['observed_eligible'] / 2
        assert receipt['diagnostics']['selected_loss']['removed_records'][0]['table'] == 'steps'


def test_whole_state_packet_rejects_a_changed_receipt_inventory_or_member(packets):
    output, summary = packets
    packet = output / summary['runs'][0]['packet']
    contents = _snapshot_tree(packet)
    validate_score_packet(dict(contents))

    def rebuilt(changes):
        changed = {**contents, **changes}
        assertion = json.loads(changed['inputs/capture-assertion.json'])
        for entry in assertion['capture_documents']:
            data = changed['inputs/capture/' + entry['path']]
            entry.update(sha256=hashlib.sha256(data).hexdigest(), size_bytes=len(data))
        changed['inputs/capture-assertion.json'] = canonical(assertion) + b'\n'
        manifest = json.loads(changed['manifest.json'])
        for entry in manifest['files']:
            data = changed[entry['path']]
            entry.update(sha256=hashlib.sha256(data).hexdigest(), size_bytes=len(data))
        changed['manifest.json'] = canonical(manifest) + b'\n'
        return changed

    pretty = lambda value: json.dumps(value, sort_keys=True, indent=2).encode() + b'\n'
    receipt = json.loads(contents['inputs/capture/r2-native-receipt.json'])
    after = json.loads(contents['inputs/capture/r2-state-after.json'])
    hidden = json.loads(json.dumps(receipt)); hidden['classes']['shared_changed'] = hidden['classes']['shared_changed'][1:]
    with pytest.raises(ValueError, match='classification differs'):
        validate_score_packet(rebuilt({'inputs/capture/r2-native-receipt.json': pretty(hidden)}))
    extra = json.loads(json.dumps(after)); extra['entries'].append(dict(extra['entries'][0], relative_path='knowledge/new-note.md', inode=1))
    with pytest.raises(ValueError, match='inventory or quiescence'):
        validate_score_packet(rebuilt({'inputs/capture/r2-state-after.json': pretty(extra)}))
    database = next(name for name in contents if name.startswith('native/capture/conversations/'))
    with pytest.raises(ValueError, match='member differs|inventory'):
        validate_score_packet(rebuilt({database: contents[database] + b'x'}))
    extract = json.loads(contents['inputs/capture/shared-store-extract.json'])
    extract['conversation_summaries_db']['row']['total_tokens'] = 5
    with pytest.raises(ValueError, match='shared-store extract'):
        validate_score_packet(rebuilt({'inputs/capture/shared-store-extract.json': canonical(extract)}))
    context = json.loads(contents['inputs/context.json']); context['complete_root'] = True
    with pytest.raises(ValueError, match='cannot be declared complete|promotes'):
        validate_score_packet(rebuilt({'inputs/context.json': canonical(context)}))


def test_private_packets_replay_to_their_recorded_receipt(packets):
    output, summary = packets
    row = summary['runs'][1]
    receipt = replay_score_package(output / row['packet'], expected_manifest_sha256=row['manifest_sha256'], os_sandboxed=True)
    stored = json.loads((output / f'{row["packet"]}-receipt.json').read_bytes())
    assert receipt['diagnostics_sha256'] == stored['diagnostics_sha256'] == row['diagnostics_sha256']
