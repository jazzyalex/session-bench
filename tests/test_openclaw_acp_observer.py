"""OpenClaw ACP observer: tool events from the ACP stream, never from native rows.

The stream fixtures are cut from the capture ``openclaw-2026-10-07-03`` (workspace path replaced, long lists shortened).
"""
import json
from pathlib import Path

import pytest

from session_bench.openclaw_acp_observer import (
    OpenClawAcpError, build_openclaw_acp_observer, edits_target, inner_command, parse_acp_stream, stream_line, turn_summary,
)

FIXTURES = Path(__file__).resolve().parent / 'fixtures/openclaw_acp'
STREAMS = {turn: (FIXTURES / f'turn-r{turn}.acp-stream.jsonl').read_bytes() for turn in (1, 2)}
LEDGERS = {turn: (FIXTURES / f'turn-r{turn}.helper-ledger.jsonl').read_bytes() for turn in (1, 2)}
WORKLOAD = json.loads((FIXTURES / 'workload-instance.json').read_text())
KEY = 'agent:main:explicit:675736f2-bac9-40d3-98a4-a0b1c93f17ab'
ACP_SESSION = '7085af1a-b79d-468a-9336-9270cd74fcc0'
RUN = 'SB_SURVIVAL_V1_RUN_openclaw-2026-10-07-03'


def observe(**options):
    options.setdefault('stream_by_turn', STREAMS)
    options.setdefault('helper_by_turn', LEDGERS)
    options.setdefault('before_sha256', 'a' * 64)
    options.setdefault('after_sha256', 'b' * 64)
    options.setdefault('session_key', KEY)
    return build_openclaw_acp_observer(workload=WORKLOAD, **options)


def edit(raw, change, *, where=lambda message: True):
    """A copy of a stream in which ``change`` was applied to every message that ``where`` accepts (None drops it)."""
    lines = []
    for line in raw.decode().splitlines():
        wrapper = json.loads(line)
        message = json.loads(wrapper['raw'])
        if where(message):
            message = change(message)
            if message is None:
                continue
        lines.append(stream_line(wrapper['dir'], json.dumps(message, ensure_ascii=False), wrapper['t_ns']))
    return b''.join(lines)


def update_kind(kind):
    return lambda message: isinstance(message.get('params'), dict) and (message['params'].get('update') or {}).get('sessionUpdate') == kind


def test_stream_lines_are_wrapped_json_rpc_messages_with_a_time_and_a_direction():
    records = parse_acp_stream(STREAMS[1])
    assert [row['dir'] for row in records[:5]] == ['send', 'recv', 'send', 'recv', 'send']
    assert records[0]['message']['method'] == 'initialize' and records[4]['message']['method'] == 'session/prompt'
    # A received line carries its arrival time. The file order is the order in which the controller handled the lines:
    # a notification that arrived before the prompt was sent can stand after it.
    assert all(type(row['t_ns']) is int for row in records) and records[5]['t_ns'] < records[4]['t_ns']
    assert parse_acp_stream(stream_line('recv', '{"jsonrpc":"2.0","id":1,"result":{}}', 5)) == [
        {'t_ns': 5, 'dir': 'recv', 'message': {'jsonrpc': '2.0', 'id': 1, 'result': {}}}]
    for bad in (b'not json\n', stream_line('recv', 'log line, not JSON', 1), stream_line('side', '{"jsonrpc":"2.0"}', 1),
                stream_line('recv', '{"id":1}', 1), b'{"t_ns": 1, "dir": "recv", "raw": "{}", "extra": 1}\n'):
        with pytest.raises(OpenClawAcpError, match='not a wrapped JSON-RPC message'):
            parse_acp_stream(bad)


def test_turn_summary_holds_each_tool_call_with_arguments_status_exit_code_id_and_order():
    first = turn_summary(STREAMS[1])
    assert first['acp_session_id'] == ACP_SESSION and first['session_keys'] == [KEY] and first['stop_reason'] == 'end_turn'
    assert first['prompt'] == WORKLOAD['turns'][0]['text'] and first['text'].endswith('SB_SURVIVAL_V1_RESPONSE_R1_cafe_🙂') and first['chunks'] == 17
    assert [(call['call_id'][:13], call['kind'], call['status'], call['raw_output']) for call in first['calls']] == [
        ('exec-66a30c16', 'execute', 'completed', {'status': 'completed', 'exitCode': 0, 'durationMs': 4}),
        ('exec-963955f7', 'execute', 'failed', {'status': 'failed', 'exitCode': 1, 'durationMs': 0})]
    assert first['calls'][0]['raw_input'] == {'command': f"/bin/zsh -lc 'python3 bench_check.py inspect --run-canary {RUN}'",
                                             'cwd': '/synthetic/workspace/fixture_project'}
    assert all(call['called_ns'] < call['ended_ns'] for call in first['calls']) and first['context_usage']['used'] == 27516
    second = turn_summary(STREAMS[2])
    assert [call['title'].split(':')[0] for call in second['calls']] == ['bash', 'apply_patch', 'bash']
    patch = second['calls'][1]
    # apply_patch is a tool call with its arguments: the path, the kind and the diff of each change. Its end has no exit code.
    assert patch['kind'] == 'other' and patch['status'] == 'completed' and edits_target(patch) and not edits_target(second['calls'][0])
    change = patch['raw_input']['changes'][0]
    assert change['path'] == '/synthetic/workspace/fixture_project/checkout.py' and change['kind'] == {'type': 'update', 'move_path': None}
    assert '+    subtotal = sum(price * quantity for price, quantity in items)' in change['diff'] and 'exitCode' not in patch['raw_output']
    assert second['permissions'] == [] and second['session_keys'] == [KEY]
    # No output text of a tool call anywhere in the stream.
    assert b'SB_SURVIVAL_V1_HELPER_' not in STREAMS[1] + STREAMS[2] and b'checkout_source' not in STREAMS[1]


def test_inner_command_takes_only_the_shell_wrapper():
    assert inner_command("/bin/zsh -lc 'python3 bench_check.py final --run-canary X'") == 'python3 bench_check.py final --run-canary X'
    assert inner_command('/bin/zsh -lc "sed -n \'1,160p\' checkout.py"') == "sed -n '1,160p' checkout.py"
    assert inner_command('python3 bench_check.py final') == 'python3 bench_check.py final'
    assert inner_command("/usr/bin/env -lc 'x'") == "/usr/bin/env -lc 'x'" and inner_command("zsh -lc 'unbalanced") == "zsh -lc 'unbalanced"


def test_observer_takes_actions_and_results_from_the_stream_and_helper_output_from_the_ledger():
    observer = observe(gateway_model={'provider': 'openai', 'model': 'gpt-5.6-terra'})
    assert observer['independent'] is True and observer['tool_events_observed'] is True and 'unobserved_metrics' not in observer
    assert observer['acp_session_id'] == ACP_SESSION and observer['session_key'] == KEY and 'no native input' in observer['method']
    events = {row['id']: row for row in observer['events']}
    scored = [row for row in observer['events'] if row['kind'] == 'action' and row['population_role'] == 'primary_scored']
    assert [row['id'] for row in scored] == ['r1-call-1', 'r1-call-2', 'r2-call-2', 'r2-call-3']
    assert all(row['source'] == 'harness_stdout' and row['fields']['call_id'].startswith('exec-') and 'observed_at' in row for row in scored)
    inspect = events['r1-call-1']['fields']
    assert inspect['command'] == f'python3 bench_check.py inspect --run-canary {RUN}' and inspect['action_kind'] == 'inspect' and inspect['tool'] == 'bash'
    assert inspect['argv'] == ['python3', 'bench_check.py', 'inspect', '--run-canary', RUN] and inspect['turn_id'] == 'turn-r1'
    baseline = events['r1-call-2:result']['fields']
    ledger = [json.loads(line) for line in LEDGERS[2].decode().splitlines()]
    assert baseline['exit_code'] == 1 and baseline['status'] == 'failure' and baseline['output'] == ledger[1]['output']
    assert baseline['output_source'] == 'helper_ledger' and baseline['call_id'] == events['r1-call-2']['fields']['call_id']
    patch = events['r2-call-2']['fields']
    assert patch['name'] == 'file_edit' and patch['tool'] == 'apply_patch' and patch['target'] == 'fixture_project/checkout.py'
    assert patch['changes'][0]['path'] == 'fixture_project/checkout.py' and patch['changes'][0]['diff'].startswith('@@ -1,5 +1,4 @@')
    assert events['r2-call-2:result']['fields']['status'] == 'success'
    # The read of the file before the edit is an unscored action with its arguments.
    read = events['r2-call-1']
    assert read['population_role'] == 'unscored' and read['metric_ids'] == [] and read['fields']['command'] == "sed -n '1,160p' checkout.py"
    assert events['r2-call-3:result']['fields']['exit_code'] == 0
    response = events['response-r2']['fields']
    assert response['text'].endswith('SB_SURVIVAL_V1_RESPONSE_R2_correction_Δ') and response['model_id'] == 'gpt-5.6-terra'
    assert response['configuration'] == {'provider': 'openai'} and 'gateway' in response['model_source'] and 'usage' not in response
    assert events['turn-r1']['fields']['text'] == WORKLOAD['turns'][0]['text'] and all(row['session_id'] == KEY for row in observer['events'])
    assert events['change-checkout']['fields']['action_id'] == 'r2-call-2'
    kinds = [row['kind'] for row in observer['relations']]
    assert kinds.count('action_result') == 4 and kinds.count('helper_for') == 3 and 'final_after' in kinds and 'supersedes' in kinds
    # Without the gateway line the observer names no model.
    assert 'model_id' not in {row['id']: row for row in observe()['events']}['response-r1']['fields']


def test_compound_helper_call_gives_one_action_per_segment():
    command = f"/bin/zsh -lc 'python3 bench_check.py inspect --run-canary {RUN} && python3 bench_check.py baseline --run-canary {RUN}'"

    def merge(message):
        update = message['params']['update']
        if update['toolCallId'].startswith('exec-66a30c16'):
            return None
        if update['sessionUpdate'] == 'tool_call':
            update['rawInput']['command'] = command
        return message

    first = edit(STREAMS[1], merge, where=lambda message: update_kind('tool_call')(message) or update_kind('tool_call_update')(message))
    observer = observe(stream_by_turn={1: first, 2: STREAMS[2]})
    actions = [row for row in observer['events'] if row['kind'] == 'action' and row['fields'].get('turn_id') == 'turn-r1']
    assert [row['id'] for row in actions] == ['r1-call-1:segment-0', 'r1-call-1:segment-1']
    results = {row['id']: row['fields'] for row in observer['events'] if row['kind'] == 'result'}
    # The call ended with exit code 1: that is the code of the last segment; the first takes its code from the ledger.
    assert results['r1-call-1:segment-0:result']['exit_code'] == 0 and results['r1-call-1:segment-1:result']['exit_code'] == 1


@pytest.mark.parametrize('turn, where, change, message', [
    (1, lambda m: m.get('id') == 3 and 'result' in m, lambda m: {**m, 'result': {'stopReason': 'cancelled'}}, 'did not end normally'),
    (1, lambda m: m.get('id') == 3 and 'result' in m, lambda m: None, 'does not close with the one response'),
    (1, update_kind('tool_call_update'), lambda m: None, 'tool call that did not end'),
    (1, update_kind('usage_update'), lambda m: {**m, 'params': {**m['params'], 'sessionId': 'another-session'}}, 'another session'),
    (1, update_kind('usage_update'), lambda m: {**m, 'params': {**m['params'], 'update': {'sessionUpdate': 'surprise'}}}, 'does not know'),
    (1, update_kind('session_info_update'), lambda m: json.loads(json.dumps(m).replace(KEY, 'agent:main:main')), 'names another'),
    (1, update_kind('agent_message_chunk'), lambda m: None, 'lacks the exact canary'),
    (1, update_kind('tool_call_update'), lambda m: json.loads(json.dumps(m).replace('"exitCode": 1', '"exitCode": 0')), 'status that differs'),
    (1, update_kind('tool_call_update'), lambda m: json.loads(json.dumps(m).replace('"exitCode": 0, ', '')), 'no exit code'),
    (1, update_kind('tool_call'), lambda m: json.loads(json.dumps(m).replace('bench_check.py inspect', 'bench_check.py final')), 'another turn'),
    (2, update_kind('tool_call'), lambda m: None if m['params']['update']['title'].startswith('apply_patch') else m, 'no open call'),
    (2, lambda m: m.get('method') == 'session/prompt', lambda m: json.loads(json.dumps(m).replace('Correction R2', 'Correction R3')), 'another prompt'),
])
def test_observer_fails_closed_on_a_stream_that_is_incomplete_or_differs_from_the_ledger(turn, where, change, message):
    streams = {**STREAMS, turn: edit(STREAMS[turn], change, where=where)}
    with pytest.raises(OpenClawAcpError, match=message):
        observe(stream_by_turn=streams)


def test_observer_needs_the_edit_and_every_helper_as_a_tool_call():
    no_patch = edit(STREAMS[2], lambda m: None, where=lambda m: (update_kind('tool_call')(m) or update_kind('tool_call_update')(m))
                    and m['params']['update']['toolCallId'].startswith('exec-452e85c2'))
    with pytest.raises(OpenClawAcpError, match='no observed edit'):
        observe(stream_by_turn={1: STREAMS[1], 2: no_patch})
    no_final = edit(STREAMS[2], lambda m: None, where=lambda m: (update_kind('tool_call')(m) or update_kind('tool_call_update')(m))
                    and m['params']['update']['toolCallId'].startswith('exec-9315bc88'))
    with pytest.raises(OpenClawAcpError, match='no observed final'):
        observe(stream_by_turn={1: STREAMS[1], 2: no_final})
    with pytest.raises(OpenClawAcpError, match='did not change'):
        observe(after_sha256='a' * 64)
    with pytest.raises(OpenClawAcpError, match='not the start'):
        observe(helper_by_turn={1: LEDGERS[1].splitlines(keepends=True)[0], 2: LEDGERS[2]})
    with pytest.raises(OpenClawAcpError, match='does not name the session key'):
        observe(session_key='agent:main:explicit:00000000-0000-4000-8000-000000000000')


def test_permission_request_and_its_answer_are_part_of_the_turn():
    request = stream_line('recv', json.dumps({'jsonrpc': '2.0', 'id': 77, 'method': 'session/request_permission', 'params': {
        'sessionId': ACP_SESSION, 'toolCall': {'toolCallId': 'exec-x'},
        'options': [{'optionId': 'allow', 'kind': 'allow_once'}, {'optionId': 'deny', 'kind': 'reject_once'}]}}), 1)
    answer = stream_line('send', json.dumps({'jsonrpc': '2.0', 'id': 77, 'result': {'outcome': {'outcome': 'selected', 'optionId': 'allow'}}}), 2)
    lines = STREAMS[2].splitlines(keepends=True)
    summary = turn_summary(b''.join(lines[:1] + [request, answer] + lines[1:]))
    assert summary['permissions'][0]['answer'] == 'allow' and summary['permissions'][0]['answer_kind'] == 'allow_once'
    with pytest.raises(OpenClawAcpError, match='without an answer'):
        turn_summary(b''.join(lines[:1] + [request] + lines[1:]))
    with pytest.raises(OpenClawAcpError, match='answers no permission request'):
        turn_summary(b''.join(lines[:1] + [answer] + lines[1:]))
    wrong = stream_line('send', json.dumps({'jsonrpc': '2.0', 'id': 77, 'result': {'outcome': {'outcome': 'selected', 'optionId': 'other'}}}), 2)
    with pytest.raises(OpenClawAcpError, match='selects no offered option'):
        turn_summary(b''.join(lines[:1] + [request, wrong] + lines[1:]))
