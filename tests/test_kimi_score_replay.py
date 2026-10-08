"""Kimi wire decoder: records of the wire log and the state file; a synthetic session and the real packets."""
import hashlib
import json
from pathlib import Path
import re

import pytest

from session_bench.kimi_score_inputs import (
    HOME_PROOF_SCHEMA, home_classes, home_files_not_copied, home_index_texts, verify_home_index, workspace_id,
)
from session_bench.kimi_wire_records import (
    FORMAT, PROTOCOL_VERSION, SESSION_DIR, STATE, STATE_VERSION, TARGET, WIRE, add_native_final_after_chain, build_kimi_density,
    decode_kimi_session, harness_identity, read_kimi_family, read_wire, record_bytes, remove_final_response, shell_outcome, wire_statements,
)
from session_bench.live_metric_comparator import compare_survival_run
from session_bench.native_density import forward_occurrences, roles_after_repeats

ROOT = Path(__file__).resolve().parents[1]
SID = 'session_11111111-2222-4333-8444-555555555555'
WORKDIR = '/private/tmp/session-bench-kimi-testtest/workspace/fixture_project'
CANARY = 'SB_SURVIVAL_V1_RUN_kimi-test'
R1, R2 = 'SB_SURVIVAL_V1_RESPONSE_R1_cafe', 'SB_SURVIVAL_V1_RESPONSE_R2_fix'
PROMPTS = ('Requirement R1: total each unit price times quantity. End with ' + R1 + '.',
           'Correction R2 supersedes only the delivery condition. Keep R1. End with ' + R2 + '.')
BEFORE = 'def checkout(items):\n    subtotal = sum(price for price, quantity in items)\n    return subtotal + 5\n'
AFTER = 'def checkout(items):\n    subtotal = sum(price * quantity for price, quantity in items)\n    return subtotal if subtotal >= 50 else subtotal + 5\n'
SYSTEM = "You are Kimi Code CLI, an interactive general AI agent running on a user's computer.\n\nAlways follow the vendor rules of this long instruction text."
ALIAS = 'moonshot-ai/kimi-k2.7-code'
compact = lambda value: json.dumps(value, ensure_ascii=False, separators=(',', ':'))  # noqa: E731
sha = lambda data: hashlib.sha256(data).hexdigest()  # noqa: E731
PORTABLE = {'complete_root': True, 'companions_present': True, 'isolated_decode': True, 'canonical_equality': True}


def helper_line(phase):
    body = {'phase': phase}
    if phase == 'inspect':
        body.update(checkout_sha256=sha(BEFORE.encode()), checkout_source=BEFORE)
    return f'SB_SURVIVAL_V1_HELPER_{phase.upper()}_{phase}-fixture-0001 ' + compact(body)


def helper(phase):
    return f'python3 bench_check.py {phase} --run-canary {CANARY}'


def shell(command, output, code=0):
    """One Bash call as (name, arguments, native result): a failure has ``isError`` and the exit code line."""
    result = {'output': output + '\n', 'durationMs': 5} if code == 0 else {
        'output': f'{output}\nCommand failed with exit code: {code}.', 'isError': True, 'durationMs': 5}
    return ('Bash', {'command': command, 'cwd': WORKDIR}, result)


class Session:
    """Builds the wire log, the state file and the stdout streams of one synthetic session as CLI 2.1.1 writes them."""

    def __init__(self):
        self.rows, self.clock, self.turn, self.calls, self.streams, self.turn_rows, self.step, self.edited = [], 1_791_000_000_000, -1, 0, [], [], 0, False
        self._add({'type': 'metadata', 'protocol_version': '1.5', 'created_at': self.clock}, time=False)
        self._add({'type': 'runtime.set_binding', 'workspaceId': workspace_id(WORKDIR), 'runtimeId': 'local', 'agentId': 'main'})
        self._add({'type': 'profile.bind', 'agentId': 'main', 'modelAlias': ALIAS, 'systemPrompt': SYSTEM, 'environmentDisclosure': {'cwd': WORKDIR}})
        self._add({'type': 'permission.set_mode', 'agentId': 'main', 'mode': 'auto'})

    def _add(self, record, time=True):
        self.clock += 7
        self.rows.append({**record, 'time': self.clock} if time else record)

    def _loop(self, event):
        self._add({'type': 'context.append_loop_event', 'agentId': 'main', 'event': event})

    def prompt(self, text):
        self.turn += 1
        self.prompt_id = f'msg_PROMPT{self.turn}'
        self.step, self.turn_rows, self.edited = 0, [], False
        self.streams.append([{'role': 'meta', 'type': 'system.version', 'version': '2.1.1'}])
        content = [{'type': 'text', 'text': text}]
        self._add({'type': 'turn.prompt', 'agentId': 'main', 'input': content, 'origin': {'kind': 'user'}, 'promptId': self.prompt_id, 'turnId': self.turn})
        self._add({'type': 'context.append_message', 'agentId': 'main',
                   'message': {'role': 'user', 'content': content, 'id': self.prompt_id, 'toolCalls': [], 'origin': {'kind': 'user'}}})
        self._add({'message': {'message': {'role': 'user', 'content': content}, 'meta': {'source': 'input', 'promptId': self.prompt_id}},
                   'type': 'agent.message.appended', 'kind': 'event'})
        self._add({'type': 'context.append_message', 'agentId': 'main',
                   'message': {'role': 'user', 'content': [{'type': 'text', 'text': '<system-reminder>\nInjected vendor reminder text of the harness.\n</system-reminder>'}],
                               'toolCalls': [], 'origin': {'kind': 'injection', 'variant': 'permission_mode'}}})
        self._add({'type': 'llm.tools_snapshot', 'agentId': 'main', 'hash': 'ab' * 32,
                   'tools': [{'name': 'Bash', 'description': 'Run a shell command with care and report the output text.',
                              'parameters': {'type': 'object', 'properties': {'command': {'type': 'string', 'description': 'The shell command line to run here.'}}}}]})
        return self

    def round(self, calls=(), *, think='thinking about the task', text=None, error=False):
        """One model request of the turn: a failed attempt, or reasoning with tool calls or with the visible text."""
        self.step += 1
        step = f'step-{self.turn}-{self.step}'
        self._loop({'type': 'step.begin', 'uuid': step, 'turnId': str(self.turn), 'step': self.step})
        self._add({'type': 'llm.request', 'agentId': 'main', 'provider': 'openai', 'model': 'kimi-k2.7-code', 'modelAlias': ALIAS,
                   'systemPromptHash': 'cd' * 32, 'toolsHash': 'ab' * 32, 'turnStep': f'{self.turn}.{self.step}'})
        if error:
            self._loop({'type': 'step.end', 'uuid': step, 'turnId': str(self.turn), 'step': self.step, 'finishReason': 'error'})
            message = '429 Your account org-0123456789abcdef0123456789abcdef<ak-abcdefghij0123456789> request reached organization max RPM: 3'
            self._add({'type': 'turn.step.retrying', 'agentId': 'main', 'turnId': self.turn, 'step': self.step, 'stepId': step, 'errorMessage': message})
            self.streams[-1].append({'role': 'meta', 'type': 'turn.step.retrying', 'error_message': message})
            return self
        usage = {'inputOther': 100 + self.step, 'output': 20, 'inputCacheRead': 1000, 'inputCacheCreation': 0}
        message_id = f'chatcmpl-{self.clock // 1000:08x}{self.turn:08x}{self.step:08x}'
        self._add({'type': 'usage.record', 'agentId': 'main', 'model': ALIAS, 'usage': usage, 'usageScope': 'turn'})
        parts = [{'type': 'think', 'think': think}]
        self._loop({'type': 'content.part', 'uuid': f'{step}-think', 'turnId': str(self.turn), 'step': self.step, 'stepUuid': step, 'part': parts[0]})
        if text is not None:
            parts.append({'type': 'text', 'text': text})
            self._loop({'type': 'content.part', 'uuid': f'{step}-text', 'turnId': str(self.turn), 'step': self.step, 'stepUuid': step, 'part': parts[1]})
            self.streams[-1].append({'role': 'assistant', 'content': text})
        listed, results = [], []
        for name, arguments, result in calls:
            call = f'{name}_{self.calls}_0000beef'
            self.calls += 1
            if name == 'Edit' and not self.edited:
                self.edited = True
                self._add({'type': 'file_history.tracked', 'agentId': 'main', 'turnId': self.turn, 'path': 'checkout.py',
                           'entry': {'key': 'file-history/' + sha(b'checkout.py') + '@v1', 'version': 1, 'contentHash': sha(BEFORE.encode()), 'size': len(BEFORE)}})
            self._loop({'type': 'tool.call', 'uuid': 'uuid-' + call, 'turnId': str(self.turn), 'step': self.step, 'stepUuid': step, 'toolCallId': call,
                        'name': name, 'args': arguments, 'display': {'kind': 'command', **arguments}})
            listed.append({'type': 'function', 'id': call, 'name': name, 'arguments': compact(arguments)})
            results.append((call, result))
        if listed:
            self.streams[-1].append({'role': 'assistant', 'tool_calls': [{'type': 'function', 'id': item['id'], 'function': {
                'name': item['name'], 'arguments': item['arguments']}} for item in listed]})
        for call, result in results:
            self._loop({'type': 'tool.result', 'parentUuid': 'uuid-' + call, 'toolCallId': call, 'result': result})
            self.streams[-1].append({'role': 'tool', 'tool_call_id': call, 'content': result['output']})
        self._loop({'type': 'step.end', 'uuid': step, 'turnId': str(self.turn), 'step': self.step, 'finishReason': 'tool_use' if calls else 'end_turn',
                    'usage': usage, 'messageId': message_id})
        self.turn_rows.append({'message': {'role': 'assistant', 'content': parts, 'toolCalls': listed}, 'meta': {'source': 'llm', 'usage': usage, 'messageId': message_id}})
        self.turn_rows += [{'message': {'role': 'tool', 'content': [{'type': 'text', 'text': result['output']}], 'toolCallId': call}, 'meta': {'source': 'tool'}}
                           for call, result in results]
        return self

    def end(self):
        for wrapper in self.turn_rows:
            self._add({'message': wrapper, 'type': 'agent.message.appended', 'kind': 'event'})
        self._add({'type': 'turn.ended', 'agentId': 'main', 'turnId': self.turn, 'reason': 'completed'})
        if self.edited:
            self._add({'type': 'file_history.checkpoint', 'agentId': 'main', 'turnId': self.turn, 'phase': 'end',
                       'entries': {'checkout.py': {'key': 'file-history/' + sha(b'checkout.py') + '@v2', 'version': 2, 'contentHash': sha(AFTER.encode()), 'size': len(AFTER)}}})
        self.streams[-1].append({'role': 'meta', 'type': 'session.resume_hint', 'session_id': SID})
        return self

    def wire(self):
        return ''.join(compact(row) + '\n' for row in self.rows).encode()

    def state(self):
        return compact({'id': SID, 'version': 2, 'cwd': WORKDIR, 'lastTurnReason': 'completed', 'createdAt': 1, 'updatedAt': 2}).encode()

    def stdout(self, turn):
        return ''.join(compact(row) + '\n' for row in self.streams[turn - 1]).encode()

    def decode(self):
        return decode_kimi_session(self.wire(), self.state())


def edit_call():
    return ('Edit', {'path': WORKDIR + '/checkout.py', 'old_string': BEFORE.rstrip('\n'), 'new_string': AFTER.rstrip('\n')},
            {'output': 'Replaced 1 occurrence in ' + WORKDIR + '/checkout.py', 'durationMs': 2})


def read_call():
    return ('Read', {'path': WORKDIR + '/checkout.py'}, {'output': '1\tdef checkout(items):', 'note': '<system>1 line read</system>', 'durationMs': 1})


def normal_session(*, retry=False, compound=False):
    session = Session().prompt(PROMPTS[0])
    if compound:
        session.round([shell(helper('inspect') + '; ' + helper('baseline'), helper_line('inspect') + '\n' + helper_line('baseline'), 1)])
    else:
        session.round([shell(helper('inspect'), helper_line('inspect'))]).round([shell(helper('baseline'), helper_line('baseline'), 1)])
    session.round(text='The baseline fails.\n\n' + R1).end()
    session.prompt(PROMPTS[1]).round([read_call()]).round([edit_call()]).round([shell(helper('final'), helper_line('final'))])
    if retry:
        session.round(error=True).round(error=True)
    return session.round(text=f'Fixed. I ran `{helper("final")}`.\n\n' + R2).end()


INSTANCE = {'turns': [{'response_canary': R1}, {'response_canary': R2}]}


def test_wire_gives_turns_responses_calls_results_and_the_header_facts():
    out = normal_session().decode()
    assert (out['status'], out['diagnostics'], out['format'], out['session_id']) == ('ok', [], FORMAT, SID)
    assert (out['protocol_version'], out['state_version'], out['harness'], out['surface']) == (PROTOCOL_VERSION, STATE_VERSION, 'Kimi Code', 'cli')
    assert [(row['id'], row['revision'], row['text']) for row in out['turns']] == [('msg_PROMPT0', 'r1', PROMPTS[0]), ('msg_PROMPT1', 'r2', PROMPTS[1])]
    first, second = out['responses']
    assert (first['turn_id'], first['status'], first['canary'], first['model_id'], first['configuration']) == (
        'msg_PROMPT0', 'completed', R1, 'kimi-k2.7-code', {'model_alias': ALIAS})
    # A response that also names the run canary of a command keeps its own response marker.
    assert second['canary'] == R2 and CANARY in second['text']
    assert [(row['id'], row.get('action_kind'), row.get('target')) for row in out['actions']] == [
        ('Bash_0_0000beef', 'inspect', None), ('Bash_1_0000beef', 'test', None), ('Read_2_0000beef', None, WORKDIR + '/checkout.py'),
        ('Edit_3_0000beef', 'edit', TARGET), ('Bash_4_0000beef', 'test', None)]
    assert out['actions'][0]['argv'] == ['python3', 'bench_check.py', 'inspect', '--run-canary', CANARY] and out['actions'][0]['cwd'] == WORKDIR
    assert [(row['kind'], row['from_id'], row['to_id']) for row in out['relations'] if row['kind'] == 'turn_response'] == [
        ('turn_response', 'msg_PROMPT0', first['id']), ('turn_response', 'msg_PROMPT1', second['id'])]
    assert all(type(row['timestamp']) is int for key in ('turns', 'responses', 'actions', 'results') for row in out[key])
    assert out['thread'][0] == {'id': 'msg_PROMPT0', 'role': 'user', 'ordinal': 5, 'parent_id': None}
    assert {row['role'] for row in out['thread']} == {'user', 'assistant', 'tool'}


def test_a_failed_shell_call_states_its_exit_code_and_a_success_has_code_zero():
    out = normal_session().decode()
    results = {row['id']: row for row in out['results']}
    inspect, baseline, edit = results['Bash_0_0000beef:result'], results['Bash_1_0000beef:result'], results['Edit_3_0000beef:result']
    # The helper line without the line end, as the helper ledger states it; the exit code line is not part of the output.
    assert (inspect['output'], inspect['status'], inspect['exit_code'], inspect['helper_nonce']) == (helper_line('inspect'), 'success', 0, 'inspect-fixture-0001')
    assert (baseline['output'], baseline['status'], baseline['exit_code']) == (helper_line('baseline'), 'failure', 1)
    # A tool other than the shell has a status and no exit code.
    assert edit['status'] == 'success' and 'exit_code' not in edit
    assert shell_outcome({'output': 'x\nCommand failed with exit code: 127.', 'isError': True}) == ('x', 'failure', 127)
    assert shell_outcome({'output': 'no code line', 'isError': True}) == ('no code line', 'failure', None)
    # The failure line alone is not a failure: ``isError`` decides.
    assert shell_outcome({'output': 'x\nCommand failed with exit code: 3.'}) == ('x\nCommand failed with exit code: 3.', 'success', 0)


def test_compound_call_is_the_record_of_each_helper_and_only_the_last_segment_takes_the_call_status():
    out = normal_session(compound=True).decode()
    parts = [row for row in out['actions'] if row.get('parent_call_id') == 'Bash_0_0000beef']
    assert [(row['id'], row['action_kind'], row['call_id']) for row in parts] == [
        ('Bash_0_0000beef:segment-0', 'inspect', 'Bash_0_0000beef'), ('Bash_0_0000beef:segment-1', 'test', 'Bash_0_0000beef')]
    results = {row['action_id']: row for row in out['results']}
    first, last = results['Bash_0_0000beef:segment-0'], results['Bash_0_0000beef:segment-1']
    # The call failed with code 1: that is the code of the last segment. The first segment has no code of its own (no echo, no && chain).
    assert (last['output'], last['exit_code'], last['status']) == (helper_line('baseline'), 1, 'failure')
    assert first['output'] == helper_line('inspect') and 'exit_code' not in first and 'status' not in first
    assert sum(row['kind'] == 'action_result' for row in out['relations']) == 5


def test_model_and_usage_join_the_response_step_and_no_total_is_declared():
    out = normal_session().decode()
    first, second = out['responses']
    # The usage of the step that wrote the response (``step.end``), each count under its own key.
    assert first['usage'] == {'input_tokens': 103, 'output_tokens': 20, 'cache_read_tokens': 1000, 'cache_write_tokens': 0}
    assert second['usage']['input_tokens'] == 104
    assert out['reconciliation'] == []
    # A missing key is not a zero.
    session = normal_session()
    for row in session.rows:
        event = row.get('event', {})
        if event.get('type') == 'step.end' and 'usage' in event:
            del event['usage']['inputCacheCreation']
    assert 'cache_write_tokens' not in session.decode()['responses'][0]['usage']


def test_changed_file_from_the_file_history_hashes_and_the_final_chain():
    out = normal_session().decode()
    add_native_final_after_chain(out, INSTANCE)
    change, = out['file_changes']
    assert (change['path'], change['native_path'], change['before_sha256'], change['after_sha256'], change['turn_id']) == (
        WORKDIR + '/checkout.py', 'checkout.py', sha(BEFORE.encode()), sha(AFTER.encode()), 'msg_PROMPT1')
    chain, = [row for row in out['relations'] if row['kind'] == 'final_after']
    assert (chain['from_id'], chain['to_id']) == ('msg_PROMPT1', 'Bash_4_0000beef') and chain['native_order_lines'] == sorted(chain['native_order_lines'])
    # No checkpoint, no changed file. A failed final test, no chain.
    session = normal_session()
    session.rows = [row for row in session.rows if row.get('type') != 'file_history.checkpoint']
    assert session.decode()['file_changes'] == []
    failed = Session().prompt(PROMPTS[0]).round(text=R1).end().prompt(PROMPTS[1]).round([edit_call()]).round(
        [shell(helper('final'), helper_line('final'), 1)]).round(text=R2).end().decode()
    add_native_final_after_chain(failed, INSTANCE)
    assert not [row for row in failed['relations'] if row['kind'] == 'final_after']


def test_a_retried_request_is_a_step_without_content_and_states_no_event():
    plain, retried = normal_session(), normal_session(retry=True)
    out = retried.decode()
    assert out['status'] == 'ok' and out['diagnostics'] == [] and out['retried_requests'] == 2
    strip = lambda rows: [{key: value for key, value in row.items() if key not in ('sequence', 'timestamp', 'locator', 'usage_locator', 'preimage_locator', 'usage', 'native_step', 'id')}
                          for row in rows]  # noqa: E731
    assert strip(out['actions']) == strip(plain.decode()['actions']) and len(out['responses']) == 2
    roles, statements = wire_statements(read_wire(retried.wire()))
    retry_lines = [index for index, (_, row) in enumerate(read_wire(retried.wire())) if row.get('type') == 'turn.step.retrying']
    assert len(retry_lines) == 2 and all(roles[index] == 'metadata' and statements[index] == () for index in retry_lines)
    # A failed step that holds content is outside the contract.
    broken = normal_session(retry=True)
    for row in broken.rows:
        if row.get('event', {}).get('type') == 'step.end' and row['event'].get('finishReason') == 'tool_use':
            row['event']['finishReason'] = 'error'
            break
    assert any(item['code'] == 'unjoined_execution' for item in broken.decode()['diagnostics'])


def test_every_event_is_stated_more_than_once_and_the_first_statement_keeps_its_role():
    rows = read_wire(normal_session().wire())
    roles, statements = wire_statements(rows)
    counts = {}
    for events in statements:
        for event in events:
            counts[event] = counts.get(event, 0) + 1
    # A prompt: turn.prompt, the context message and the end-of-turn copy. Every other event: its loop record and the end-of-turn copy.
    assert counts['message:msg_PROMPT0'] == counts['message:msg_PROMPT1'] == 3
    assert {count for event, count in counts.items() if not event.startswith('message:msg_PROMPT')} == {2}
    # A call whose only argument is a path is restated by the copy, because the copy also holds the tool name.
    assert counts['call:Read_2_0000beef'] == 2 and not any('line-' in event for event in counts)
    final = roles_after_repeats(roles, statements)
    by_type = {}
    for (_, row), role in zip(rows, final):
        key = row['type'] + (':' + row['event']['type'] + ':' + row['event'].get('part', {}).get('type', '') if 'event' in row else '')
        by_type.setdefault(key, set()).add(role)
    assert by_type['agent.message.appended'] == {'snapshot'} and by_type['turn.prompt'] == {'user_message'}
    # The context copy of a prompt is a snapshot; an injected message is context of the harness, not a prompt.
    assert by_type['context.append_message'] == {'snapshot', 'system'}
    assert by_type['context.append_loop_event:content.part:think'] == {'explanation'} and by_type['context.append_loop_event:tool.call:'] == {'tool_call'}
    assert by_type['llm.tools_snapshot'] == by_type['profile.bind'] == {'system'} and by_type['metadata'] == {'session'}
    assert by_type['file_history.tracked'] == by_type['file_history.checkpoint'] == {'file_change'}
    occurrences = forward_occurrences([f'line-{number}' for number, _ in rows], statements)
    assert len(occurrences) == sum(counts.values()) and len({occurrence for _, occurrence in occurrences}) == len(occurrences)


def test_family_counts_every_record_and_the_outside_files_by_size(tmp_path):
    session = normal_session()
    native = tmp_path / 'native'
    files = {SESSION_DIR + '/' + WIRE: session.wire(), SESSION_DIR + '/' + STATE: session.state(),
             SESSION_DIR + '/logs/kimi-code.log': b'a log line that the decoder never opens\n', SESSION_DIR + '/notify/state.json': b'{"enabled":false}'}
    for name, data in files.items():
        (native / name).parent.mkdir(parents=True, exist_ok=True)
        (native / name).write_bytes(data)
    artifacts = [{'path': name, 'sha256': sha(data), 'size_bytes': len(data)} for name, data in sorted(files.items())]
    records, locators, exceptions, occurrences = read_kimi_family(native, artifacts=artifacts)
    rows = read_wire(session.wire())
    assert exceptions == [] and len(records) == len(rows) + 3 and len(locators) == len(records)
    by_id = {row['record_id']: row for row in records}
    assert by_id['outside-the-read:session/logs/kimi-code.log'] == {
        'record_id': 'outside-the-read:session/logs/kimi-code.log', 'record_kind': 'metadata', 'logical_bytes': 40, 'classification': 'unclassified'}
    assert by_id['session/state.json']['record_kind'] == 'session'
    assert by_id[f'session/{WIRE}:line-1']['logical_bytes'] == record_bytes(rows[0][1])
    density = build_kimi_density(native, artifacts=artifacts, complete_record_family=True).evidence
    assert density['evidence_complete'] is True and density['records'] == records
    assert build_kimi_density(native, artifacts=artifacts, complete_record_family=False).evidence['evidence_complete'] is False
    with pytest.raises(ValueError, match='differs from its inventory'):
        read_kimi_family(native, artifacts=[{**row, 'sha256': '0' * 64} if row['path'].endswith(WIRE) else row for row in artifacts])
    # The outside files are never opened: a missing one changes nothing.
    (native / SESSION_DIR / 'logs/kimi-code.log').unlink()
    assert read_kimi_family(native, artifacts=artifacts)[0] == records


@pytest.mark.parametrize('change,code', [
    (lambda rows: rows.append({'type': 'brand.new_record', 'time': 1}), 'unknown_record'),
    (lambda rows: rows[0].pop('protocol_version'), 'unsupported_schema'),
    (lambda rows: next(row for row in rows if row.get('event', {}).get('type') == 'tool.result')['event'].update(parentUuid='other'), 'unjoined_execution'),
    (lambda rows: rows.remove(next(row for row in rows if row.get('event', {}).get('type') == 'tool.result')), 'unjoined_execution'),
    (lambda rows: next(row for row in rows if row.get('event', {}).get('type') == 'content.part')['event']['part'].update(type='image'), 'unknown_record'),
])
def test_a_shape_outside_the_contract_is_a_diagnostic(change, code):
    session = normal_session()
    change(session.rows)
    out = session.decode()
    assert code in [item['code'] for item in out['diagnostics']]
    assert out['status'] == ('ok' if code == 'unjoined_execution' else 'unsupported')


def test_a_bad_document_is_refused_without_an_exception():
    session = normal_session()
    assert decode_kimi_session(b'{"type":"metadata"\n', session.state())['status'] == 'unsupported'
    assert decode_kimi_session(session.wire(), b'[]')['diagnostics'][0]['code'] == 'malformed_record'
    assert harness_identity('Some other agent.') == (None, None) and harness_identity(None) == (None, None)


def test_loss_control_removes_the_final_response_and_its_copy_and_the_decode_stays_clean():
    session = normal_session()
    damaged, removed = remove_final_response(session.wire(), R2)
    assert [row['type'] for row in removed] == ['context.append_loop_event', 'agent.message.appended'] and len(damaged) < len(session.wire())
    out = decode_kimi_session(damaged, session.state())
    assert out['status'] == 'ok' and out['diagnostics'] == [] and [row['canary'] for row in out['responses']] == [R1]
    with pytest.raises(ValueError, match='one final response'):
        remove_final_response(damaged, R2)


def test_home_index_files_are_rebuilt_from_public_values_and_checked_against_the_listing():
    plan = {'attempt_id': 'kimi-test', 'fixture': WORKDIR, 'kimi_code_home': '/private/tmp/session-bench-kimi-testtest/kimi'}
    workspace = workspace_id(WORKDIR)
    assert workspace == 'wd_fixture_project_' + sha(WORKDIR.encode())[:12]
    texts = home_index_texts(plan, SID, touched_at=1791000000123, created_at='2026-10-08T02:21:14.443Z', last_opened_at='2026-10-08T02:22:37.291Z')
    listing = {path: {'sha256': sha(text.encode()), 'size_bytes': len(text.encode())} for path, text in texts.items()}
    prefix = f'sessions/{workspace}/{SID}/'
    listing.update({prefix + 'agents/main/wire.jsonl': {'sha256': 'aa' * 32, 'size_bytes': 10}, prefix + 'state.json': {'sha256': 'bb' * 32, 'size_bytes': 5},
                    f'sessions/.index-dirty/{SID}.1791000000001': {'sha256': sha(b''), 'size_bytes': 0},
                    'cache/query-store/shard-00/db.wal': {'sha256': sha(b''), 'size_bytes': 0},
                    'cache/query-store/shard-03/db.wal': {'sha256': 'cc' * 32, 'size_bytes': 1333}, 'device_id': {'sha256': 'dd' * 32, 'size_bytes': 36}})
    classes = home_classes(listing, plan, SID)
    assert sorted(classes['session']) == ['agents/main/wire.jsonl', 'state.json'] and sorted(classes['index']) == sorted(texts)
    assert classes['other'] == ['cache/query-store/shard-03/db.wal', 'device_id'] and len(classes['empty']) == 2
    assert home_files_not_copied(listing, plan, SID) == [{'path': 'cache/query-store/shard-03/db.wal', 'size_bytes': 1333}, {'path': 'device_id', 'size_bytes': 36}]
    proof = {'schema_version': HOME_PROOF_SCHEMA, 'run_id': 'kimi-test', 'session_id': SID,
             'files': [{'path': path, 'text': text, 'sha256': sha(text.encode()), 'size_bytes': len(text.encode())} for path, text in sorted(texts.items())]}
    assert verify_home_index(proof, plan, SID, listing) is True
    # Another text with the right digest in the proof, but not in the listing, is refused; so is another value.
    other = json.loads(json.dumps(proof))
    other['files'][1]['text'] = other['files'][1]['text'].replace(SID, 'session_99999999-2222-4333-8444-555555555555')
    other['files'][1]['sha256'] = sha(other['files'][1]['text'].encode())
    with pytest.raises(ValueError, match='differs from the home listing'):
        verify_home_index(other, plan, SID, listing)
    with pytest.raises(ValueError, match='outside the known layout'):
        home_classes({**listing, 'sessions/wd_other_000000000000/session_x/state.json': {'sha256': 'ee' * 32, 'size_bytes': 1}}, plan, SID)


# --- a failed turn, and every capture of the observation window ---
FAILED_TURN = ROOT / 'tests/fixtures/kimi_failed_turn'
CAPTURES = ROOT / 'artifacts/v1-expanded-preparation/live-captures'
WINDOW = ('kimi-2026-10-07-01', 'kimi-2026-10-07-02', 'kimi-2026-10-08-01', 'kimi-2026-10-08-02', 'kimi-2026-10-08-03', 'kimi-2026-10-08-04',
          'kimi-2026-10-08-05', 'kimi-2026-10-08-06')


def test_a_failed_turn_decodes_under_the_contract_and_has_no_response():
    """The fixture is the wire log and the state file of the unused attempt ``kimi-2026-10-07-01`` after turn 2 (a provider rate limit
    ended the turn). It was written with the public sanitizer: vendor text blanked, organisation id and key id aliased. The tool
    definitions are cut to their names."""
    wire, state = (FAILED_TURN / 'wire.jsonl').read_bytes(), (FAILED_TURN / 'state.json').read_bytes()
    assert not re.search(rb'org-[0-9a-f]{8}|ak-[0-9a-z]{8}', wire) and b'org-XXXXXXXX' in wire
    rows = read_wire(wire)
    interrupted = [(index, row) for index, (_, row) in enumerate(rows) if row['type'] == 'turn.step.interrupted']
    assert len(interrupted) == 1 and sorted(interrupted[0][1]) == ['agentId', 'message', 'reason', 'step', 'time', 'turnId', 'type']
    assert [row.get('reason') for _, row in rows if row['type'] == 'turn.ended'] == ['completed', 'failed']
    out = decode_kimi_session(wire, state)
    assert (out['status'], out['diagnostics'], out['interrupted_steps'], out['protocol_version']) == ('ok', [], 1, PROTOCOL_VERSION)
    # Both prompts and every call and result before the error are decoded. The failed turn has no response.
    assert len(out['turns']) == 2 and len(out['actions']) == len(out['results']) == 5
    assert [row['turn_id'] for row in out['responses']] == [out['turns'][0]['id']] and out['responses'][0]['status'] == 'completed'
    # The record states no event: it is metadata, and no duplicate.
    roles, statements = wire_statements(rows)
    assert roles[interrupted[0][0]] == 'metadata' and statements[interrupted[0][0]] == () and 'unknown' not in roles
    # No final-after relation for a turn without a response.
    add_native_final_after_chain(out, {'turns': [{'response_canary': 'SB_SURVIVAL_V1_RESPONSE_R1_cafe_🙂'}, {'response_canary': 'SB_SURVIVAL_V1_RESPONSE_R2_correction_Δ'}]})
    assert not [row for row in out['relations'] if row['kind'] == 'final_after']


@pytest.mark.skipif(not (CAPTURES / WINDOW[0]).is_dir(), reason='Kimi captures are not in this checkout')
@pytest.mark.parametrize('attempt', WINDOW)
def test_every_capture_of_the_observation_window_decodes_under_the_contract(attempt):
    """All eight attempts of 2026-10-07 and 2026-10-08 (three scored, five unused), each native copy of each turn."""
    copies = sorted((CAPTURES / attempt).glob('turn-r*/native-session'))
    assert copies
    for native in copies:
        wire, state = (native / WIRE).read_bytes(), (native / STATE).read_bytes()
        out = decode_kimi_session(wire, state)
        assert out['status'] == 'ok' and out['diagnostics'] == [], (attempt, native.parent.name)
        assert out['protocol_version'] == PROTOCOL_VERSION and out['state_version'] == STATE_VERSION
        assert 'unknown' not in wire_statements(read_wire(wire))[0]
        # A turn that failed has an interrupted step and no response; every other turn has one response.
        failed = sum(row.get('reason') == 'failed' for _, row in read_wire(wire) if row['type'] == 'turn.ended')
        assert out['interrupted_steps'] == failed and len(out['responses']) == len(out['turns']) - failed


# --- the real packets ---
RUNS = ('kimi-2026-10-08-01', 'kimi-2026-10-08-02', 'kimi-2026-10-08-06')
PRIVATE = ROOT / 'artifacts/v1-expanded-preparation/kimi-score-replay-v4'
real = pytest.mark.skipif(not (PRIVATE / 'summary.json').is_file(), reason='Kimi private packets are not in this checkout')
ABSENT = {'attribution.reconciliation', 'broad.naive_reader_duplicate_safety'}


@real
def test_31_metric_rows_of_each_run():
    summary = json.loads((PRIVATE / 'summary.json').read_bytes())
    assert [row['run_id'] for row in summary['runs']] == list(RUNS)
    for row in summary['runs']:
        assert row['resolved_metric_count'] == 31 and row['unresolved_metric_ids'] == [] and row['selected_loss_detected'] is True
        assert row['tamper_controls'] == 'passed' and row['os_sandboxed'] is True
        metrics = {item['id']: item for item in json.loads((PRIVATE / f"{row['run_id']}-receipt.json").read_bytes())['diagnostics']['intact']['metrics']}
        assert {name for name, item in metrics.items() if item['state'] == 'native_absent'} == ABSENT
        assert all(item['state'] == 'measured' for name, item in metrics.items() if name not in ABSENT)
        assert metrics['work.actions']['correct'] == metrics['work.results']['correct'] == metrics['causal.action_result']['correct'] == 4
        duplicate = metrics['broad.naive_reader_duplicate_safety']
        assert duplicate['correct'] == 0 and duplicate['decoded_eligible'] > 2 * duplicate['observed_eligible']


@real
@pytest.mark.parametrize('run', RUNS)
def test_real_wire_decodes_under_the_contract(run):
    native = PRIVATE / run / 'native' / SESSION_DIR
    out = decode_kimi_session((native / WIRE).read_bytes(), (native / STATE).read_bytes())
    assert out['status'] == 'ok' and out['diagnostics'] == [] and out['harness'] == 'Kimi Code' and out['protocol_version'] == PROTOCOL_VERSION
    assert len(out['turns']) == 2 and len(out['responses']) == 2 and len(out['file_changes']) == 1
    # The hashes of the wire log are the SHA-256 of the file copies beside it (the decoder itself never opens them).
    change = out['file_changes'][0]
    copies = sorted((native / 'agents/main/file-history').iterdir())
    assert [sha(path.read_bytes()) for path in copies] == [change['before_sha256'], change['after_sha256']]
    observer = json.loads((PRIVATE / run / 'inputs/observer.json').read_bytes())
    add_native_final_after_chain(out, json.loads((PRIVATE / run / 'inputs/workload.json').read_bytes()))
    measurement = compare_survival_run(observer, out, PORTABLE, configuration_id='kimi', repetition=1)
    states = {row['id']: row['state'] for row in measurement['metrics']}
    assert states.pop('attribution.reconciliation') == 'native_absent' and set(states.values()) == {'measured'}
