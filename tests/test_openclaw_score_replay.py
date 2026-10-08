"""OpenClaw rows decoder: transcript and trace rows of the agent store; synthetic exports and the real packets."""
import hashlib
import json
from pathlib import Path
import shlex

import pytest

from session_bench.native_density import forward_occurrences, roles_after_repeats
from session_bench.openclaw_session_rows import (
    AGENT_STORE, COLUMNS, FORMAT, ROWS_FILE, ROWS_SCHEMA, SCHEMA_FILE, OpenClawRowsError, add_native_final_after_chain,
    build_openclaw_density, decode_openclaw_session_rows, inner_command, pair_script_calls, read_openclaw_family, read_rows, read_statements,
    remove_final_response, result_object_text, result_restates, script_restates, transcript_items,
)

ROOT = Path(__file__).resolve().parents[1]
SID = '32a77241-b34f-4f87-9370-7fef48a30001'
KEY_ID = 'aac1d9ac-64c1-465d-8c73-90783f950002'
KEY = 'agent:main:explicit:' + KEY_ID
TID = '01a117ed-596d-7121-87c1-b005a1c80003'
ACP = '0c70bc49-08b7-4efd-baba-28f066910004'
RUNS = ('9e844053-f653-440c-9aba-954ca6e10005', '69bcba20-ecc4-4194-910e-649add030006')
CANARY = 'SB_SURVIVAL_V1_RUN_openclaw-test'
R1, R2 = 'SB_SURVIVAL_V1_RESPONSE_R1_cafe', 'SB_SURVIVAL_V1_RESPONSE_R2_fix'
PROMPTS = ('Requirement R1: total each unit price times quantity. End with ' + R1 + '. ' + 'Context padding. ' * 4,
           'Correction R2 supersedes only the delivery condition. End with ' + R2 + '.')
WORKDIR = '/synthetic/workspace/fixture_project'
BEFORE = 'def checkout(items):\n    subtotal = sum(price for price, quantity in items)\n    return subtotal + 5\n'
AFTER = 'def checkout(items):\n    subtotal = sum(price * quantity for price, quantity in items)\n    return subtotal if subtotal >= 50 else subtotal + 5\n'
DIFF = ('@@ -1,3 +1,3 @@\n def checkout(items):\n-    subtotal = sum(price for price, quantity in items)\n-    return subtotal + 5\n'
        '+    subtotal = sum(price * quantity for price, quantity in items)\n+    return subtotal if subtotal >= 50 else subtotal + 5\n')
PATCH = ('*** Begin Patch\n*** Update File: ' + WORKDIR + '/checkout.py\n@@\n def checkout(items):\n'
         '-    subtotal = sum(price for price, quantity in items)\n-    return subtotal + 5\n'
         '+    subtotal = sum(price * quantity for price, quantity in items)\n+    return subtotal if subtotal >= 50 else subtotal + 5\n*** End Patch')
compact = lambda value: json.dumps(value, ensure_ascii=False, separators=(',', ':'))  # noqa: E731
sha = lambda data: hashlib.sha256(data).hexdigest()  # noqa: E731


def helper_line(phase):
    body = {'phase': phase}
    if phase == 'inspect':
        body.update(checkout_sha256=sha(BEFORE.encode()), checkout_source=BEFORE)
    return f'SB_SURVIVAL_V1_HELPER_{phase.upper()}_{phase}-fixture-0001 ' + compact(body)


def helper(phase):
    return f'python3 bench_check.py {phase} --run-canary {CANARY}'


class Session:
    """Builds the rows of one synthetic session as the agent store holds them."""

    def __init__(self):
        self.events, self.trace, self.turn, self.previous, self.run, self.messages, self.exec = [], [], 0, None, None, [], 0
        self.events.append({'type': 'session', 'version': 4, 'id': SID, 'timestamp': '2026-10-07T19:53:08.028Z', 'cwd': '/synthetic/workspace'})

    def _trace(self, kind, data):
        self.trace.append({'traceSchema': 'openclaw-trajectory', 'schemaVersion': 1, 'traceId': SID, 'source': 'runtime', 'type': kind,
                           'ts': f'2026-10-07T19:53:{10 + len(self.trace):02d}.000Z', 'seq': len(self.trace) + 1, 'sessionId': SID, 'sessionKey': KEY,
                           'runId': self.run, 'data': data})

    def _message(self, message, **meta):
        identity = f'm{len(self.events):03d}-0000-4000-8000-00000000{len(self.events):04d}'
        body = {**message, '__openclaw': {'runId': self.run, 'mirrorOrigin': 'codex-app-server', 'mirrorSourceFingerprint': 'ab' * 16, **meta}}
        self.events.append({'type': 'message', 'id': identity, 'parentId': self.previous,
                            'timestamp': f'2026-10-07T19:53:{10 + len(self.events):02d}.{len(self.events):03d}Z', 'message': body})
        self.previous = identity
        self.messages.append(body)
        return identity

    def _assistant(self, content, usage=None, **meta):
        zero = {'input': 0, 'output': 0, 'cacheRead': 0, 'cacheWrite': 0, 'totalTokens': 0}
        return self._message({'role': 'assistant', 'content': [content], 'api': 'openai-responses', 'provider': 'openai', 'model': 'gpt-test',
                              'usage': usage or zero, 'stopReason': 'toolUse' if content['type'] == 'toolCall' else 'stop'}, **meta)

    def user(self, text):
        self.run, self.messages = RUNS[self.turn], []
        self.turn += 1
        self._message({'role': 'user', 'content': text, 'timestamp': 1791402785986}, senderIsOwner=True)
        self._trace('session.started', {'sessionFile': KEY, 'threadId': TID, 'authProfileId': 'openai:owner.name@gmail.com', 'toolCount': 2})
        self._trace('context.compiled', {'systemPrompt': '[Oversized diagnostic JSON redacted]', 'prompt': text, 'imagesCount': 0, 'tools': [
            {'name': 'agents_list', 'description': 'List the configured agents and say which of them a session may start as a sub-agent today.',
             'parameters': {'type': 'object', 'properties': {'limit': {'type': 'number', 'description': 'The largest number of agents that the list returns in one call.'}}}}]})
        self._trace('prompt.submitted', {'threadId': TID, 'prompt': text})

    def text(self, text, usage=None):
        self._assistant({'type': 'text', 'text': text}, usage, **({'runTerminal': True} if usage else {}))
        if usage:
            self._trace('model.completed', {'threadId': TID, 'usage': {**usage, 'total': usage['totalTokens']}, 'assistantTexts': [text],
                                            'messagesSnapshot': list(self.messages)})
            self._trace('session.ended', {'status': 'success', 'threadId': TID})

    def shell(self, command, output, exit_code=0, *, script=True, trace_exit=True):
        self.exec += 1
        call, wrapped = f'exec-0000000{self.exec}-25fe-423a-96f0-c4fc196e0000', '/bin/zsh -lc ' + shlex.quote(command)
        arguments = {'command': wrapped, 'cwd': WORKDIR}
        self._assistant({'type': 'toolCall', 'id': call, 'name': 'bash', 'arguments': arguments})
        self._trace('tool.call', {'threadId': TID, 'toolCallId': call, 'name': 'bash', 'arguments': arguments})
        self._message({'role': 'toolResult', 'toolCallId': call, 'toolName': 'bash', 'isError': exit_code != 0, 'content': [{'type': 'text', 'text': output + '\n'}]},
                      toolOutput={'source': 'execution', 'modelInput': 'unverified'})
        result = {'status': 'failed' if exit_code else 'completed', 'durationMs': 3, **({'exitCode': exit_code} if trace_exit else {})}
        self._trace('tool.result', {'threadId': TID, 'toolCallId': call, 'name': 'bash', 'status': result['status'], 'isError': exit_code != 0,
                                    'result': result, 'output': output})
        if script:
            source = 'const r = await tools.exec_command(' + compact({'cmd': command, 'workdir': WORKDIR, 'yield_time_ms': 30000}) + ');\ntext(r.output);\n'
            self._script(source, json.dumps([{'type': 'input_text', 'text': 'Script completed\nOutput:\n'}, {'type': 'input_text', 'text': output + '\n'}], indent=2))
        return call

    def _script(self, source, output):
        call = f'call_{self.exec:032d}'
        self._assistant({'type': 'toolCall', 'id': call, 'name': 'exec', 'arguments': {'input': source}})
        self._message({'role': 'toolResult', 'toolCallId': call, 'toolName': 'exec', 'isError': False, 'content': [{'type': 'text', 'text': output}]},
                      toolOutput={'source': 'provider-response', 'modelInput': 'unverified'})
        return call

    def patch(self, diff=DIFF, patch=PATCH, *, error=False):
        self.exec += 1
        call = f'exec-0000000{self.exec}-5a6c-4b04-9e3e-b04295040000'
        path = WORKDIR + '/checkout.py'
        arguments = {'changes': [{'path': path, 'kind': {'type': 'update', 'move_path': None}, 'stat': {'added': 2, 'removed': 2}, 'diff': diff}]}
        done = {'status': 'failed' if error else 'completed', 'changes': [{'path': path, 'kind': {'type': 'update', 'move_path': None}}]}
        self._assistant({'type': 'toolCall', 'id': call, 'name': 'apply_patch', 'arguments': arguments})
        self._trace('tool.call', {'threadId': TID, 'toolCallId': call, 'name': 'apply_patch', 'arguments': arguments})
        self._message({'role': 'toolResult', 'toolCallId': call, 'toolName': 'apply_patch', 'isError': error, 'content': [{'type': 'text', 'text': json.dumps(done, indent=2)}]},
                      toolOutput={'source': 'execution', 'modelInput': 'unverified'})
        self._trace('tool.result', {'threadId': TID, 'toolCallId': call, 'name': 'apply_patch', 'status': done['status'], 'isError': error, 'result': done})
        self._script('const r = await tools.apply_patch(' + json.dumps(patch) + ');\ntext("");\n',
                     json.dumps([{'type': 'input_text', 'text': 'Script completed\nOutput:\n'}, {'type': 'input_text', 'text': ''}], indent=2))
        return call

    def export(self, *, compress_first=True, change=None):
        from compression import zstd
        transcript = []
        for seq, event in enumerate(self.events):
            text = compact(event)
            values = {'session_id': SID, 'seq': seq, 'event_json': text, 'created_at': 1791402788000 + seq, 'event_zstd': None,
                      'event_utf8_bytes': len(text.encode()), 'navigation_json': None}
            if compress_first and seq == 1:
                values.update(event_json=None, event_zstd={'blob_hex': zstd.compress(text.encode()).hex()})
            transcript.append({'rowid': seq + 1, 'selected_by': ['exact:session_id'], 'values': values})
        trace = [{'rowid': seq + 1, 'selected_by': ['exact:session_id'],
                  'values': {'session_id': SID, 'seq': seq, 'run_id': event['runId'], 'event_json': compact(event), 'created_at': 1791402790000 + seq}}
                 for seq, event in enumerate(self.trace)]
        window = {name: None for name in COLUMNS['session_windows']}
        window.update(session_id=SID, session_key=KEY, created_at=1791402787119, agent_harness_id='codex', chat_type='direct',
                      model='gpt-test', model_provider='openai', status='done')
        table = lambda name, columns, rows: {'table': name, 'columns': columns, 'column_types': {}, 'primary_key': [], 'has_rowid': True, 'rows': rows}  # noqa: E731
        outside = lambda name, values: table(name, sorted(values), [{'rowid': 1, 'selected_by': ['exact:x'], 'values': values}])  # noqa: E731
        stores = [
            {'store': AGENT_STORE, 'user_version': 24, 'schema_sha256': 'a' * 64, 'tables': [
                outside('session_nodes', {'session_key': KEY, 'current_session_id': SID, 'entry_json': compact({'authProfileOverride': 'openai:owner.name@gmail.com'})}),
                outside('session_entry_snapshots', {'session_key': KEY, 'field': 'skillsSnapshot',
                                                    'value_json': compact({'prompt': 'The following skills of the owner give special instructions for private tasks every day.'})}),
                outside('session_transcript_fts', {'session_id': SID, 'text': PROMPTS[0], 'payload': {'blob_hex': 'c0ffee'}}),
                table('session_windows', list(COLUMNS['session_windows']), [{'rowid': 1, 'selected_by': ['exact:session_id'], 'values': window}]),
                table('trajectory_runtime_events', list(COLUMNS['trajectory_runtime_events']), trace),
                table('transcript_events', list(COLUMNS['transcript_events']), transcript)]},
            {'store': 'agents/main/agent/codex-home/state_5.sqlite', 'user_version': 0, 'schema_sha256': 'b' * 64, 'tables': [
                outside('threads', {'id': TID, 'creator_user_id': 'user-PRIVATEACCOUNT0123456789', 'git_origin_url': 'https://github.com/owner-account/workspace.git',
                                    'first_user_message': PROMPTS[0], 'tokens_used': 5})]},
            {'store': 'state/openclaw.sqlite', 'user_version': 19, 'schema_sha256': 'c' * 64, 'tables': [
                outside('acp_replay_events', {'session_id': ACP, 'session_key': KEY, 'seq': 1, 'update_json': compact({'sessionUpdate': 'user_message_chunk', 'text': PROMPTS[0]})})]}]
        document = {'schema_version': ROWS_SCHEMA, 'session_id': SID, 'thread_id': TID, 'session_key': KEY, 'key_id': KEY_ID, 'acp_session_id': ACP, 'stores': stores}
        schema = {'stores': [{'store': store['store'], 'application_id': 0, 'user_version': store['user_version'], 'objects': [],
                              'tables': [{'name': name, 'columns': list(columns)} for name, columns in COLUMNS.items()] if store['store'] == AGENT_STORE else []}
                             for store in stores]}
        if change is not None:
            change(document, schema)
        encode = lambda value: json.dumps(value, ensure_ascii=False, sort_keys=True, indent=1).encode() + b'\n'  # noqa: E731
        return encode(document), encode(schema)


USAGE = ({'input': 100, 'output': 20, 'cacheRead': 300, 'cacheWrite': 0, 'totalTokens': 420},
         {'input': 10, 'output': 30, 'cacheRead': 900, 'cacheWrite': 0, 'totalTokens': 940})


def normal_session(*, compound=True, read_before=False, **options):
    session = Session()
    session.user(PROMPTS[0])
    session.text('I run the two checks.')
    if compound:
        session.shell(helper('inspect') + ' && ' + helper('baseline'), helper_line('inspect') + '\n' + helper_line('baseline'), 1, **options)
    else:
        session.shell(helper('inspect'), helper_line('inspect'), **options)
        session.shell(helper('baseline'), helper_line('baseline'), 1, **options)
    session.text('Baseline fails. ' + R1, USAGE[0])
    session.user(PROMPTS[1])
    session.text('I patch the file.')
    if read_before:
        session.shell("sed -n '1,20p' checkout.py", BEFORE.rstrip('\n'))
    session.patch()
    session.shell(helper('final'), helper_line('final'), **options)
    session.text('Fixed. ' + R2, USAGE[1])
    return session


INSTANCE = {'turns': [{'response_canary': R1}, {'response_canary': R2}], 'run_canary': CANARY}


def decode(session, **options):
    rows, schema = session.export(**options)
    out = decode_openclaw_session_rows(rows, schema, run_canary=CANARY)
    add_native_final_after_chain(out, INSTANCE)
    return out


def test_rows_give_turns_responses_calls_results_and_the_header_facts():
    out = decode(normal_session())
    assert out['status'] == 'ok' and out['diagnostics'] == [] and out['format'] == FORMAT and out['session_id'] == SID
    # The first prompt is the compressed row; its text is exact.
    assert [row['text'] for row in out['turns']] == list(PROMPTS) and [row['run_id'] for row in out['turns']] == list(RUNS)
    assert [(row['phase'], row.get('status'), row.get('canary')) for row in out['responses']] == [
        ('commentary', None, None), ('final_answer', 'completed', R1), ('commentary', None, None), ('final_answer', 'completed', R2)]
    assert all(row['model_id'] == 'gpt-test' and row['configuration'] == {'provider': 'openai'} for row in out['responses'])
    assert (out['transcript_version'], out['trace_schema'], out['trace_version'], out['user_version']) == (4, 'openclaw-trajectory', 1, 24)
    assert out['harness'] == 'openclaw' and out['surface'] == 'agent harness codex; chat type direct' and out['model'] == 'gpt-test'
    # Parents are explicit: one linear chain.
    assert [row['parent_id'] for row in out['thread']][0] is None and all(
        later['parent_id'] == earlier['id'] for earlier, later in zip(out['thread'], out['thread'][1:]))
    assert all(row['timestamp'].endswith('Z') for row in out['turns'] + out['responses'] + out['actions'] + out['results'])


def test_compound_call_is_the_record_of_each_helper_and_exit_codes_come_from_the_trace():
    out = decode(normal_session())
    actions = {row['id']: row for row in out['actions']}
    first, second = [row for row in out['actions'] if row.get('parent_call_id')]
    # Each segment keeps the call id of its call, so the observed call id finds both.
    assert first['call_id'] == second['call_id'] == first['parent_call_id'] and first['id'].endswith(':segment-0')
    assert (first['action_kind'], second['action_kind']) == ('inspect', 'test') and second['argv'] == ['python3', 'bench_check.py', 'baseline', '--run-canary', CANARY]
    results = {row['action_id']: row for row in out['results']}
    # First helper: exit 0 by the && chain. Second helper: the call's own exit code, from the trace row.
    assert (results[first['id']]['exit_code'], results[first['id']]['status']) == (0, 'success')
    assert (results[second['id']]['exit_code'], results[second['id']]['status'], results[second['id']]['helper_nonce']) == (1, 'failure', 'baseline-fixture-0001')
    assert results[second['id']]['output'] == helper_line('baseline')
    final = next(row for row in out['actions'] if row.get('argv', [None, None, None])[2] == 'final')
    assert results[final['id']]['exit_code'] == 0 and results[final['id']]['output'] == helper_line('final') and final['command'] == helper('final')
    # The script-form calls are second statements, not actions.
    assert {row['name'] for row in out['actions']} == {'bash', 'apply_patch'} and len(actions) == 4
    assert [row['kind'] for row in out['relations']].count('action_result') == 4


def test_separate_calls_and_an_unscored_read():
    out = decode(normal_session(compound=False, read_before=True))
    kinds = [row.get('action_kind') for row in out['actions']]
    assert kinds == ['inspect', 'test', None, 'edit', 'test'] and out['status'] == 'ok'
    results = {row['action_id']: row for row in out['results']}
    assert [results[row['id']].get('exit_code') for row in out['actions']] == [0, 1, 0, None, 0]
    assert out['actions'][2]['command'] == "sed -n '1,20p' checkout.py" and [row['kind'] for row in out['relations']][-1] == 'final_after'


def test_without_an_exit_code_in_the_trace_a_single_helper_has_a_status_and_no_exit_code():
    out = decode(normal_session(compound=False, trace_exit=False))
    results = [row for row in out['results'] if row.get('helper_nonce')]
    assert [row.get('exit_code') for row in results] == [None, None, None] and [row['status'] for row in results] == ['success', 'failure', 'success']
    # The final test has no exit code: the final-after chain is not proved.
    assert 'final_after' not in [row['kind'] for row in out['relations']]


def test_usage_sits_on_the_final_response_and_the_run_total_is_not_reconciled():
    out = decode(normal_session())
    finals = [row['id'] for row in out['responses'] if row['phase'] == 'final_answer']
    assert [row['response_id'] for row in out['usage']] == finals
    assert out['usage'][0]['usage'] == {'input_tokens': 100, 'output_tokens': 20, 'cache_read_tokens': 300, 'cache_write_tokens': 0}
    # One record holds the whole total, the others hold zeros: the sum is the total added to zeros, not a check.
    assert out['reconciliation'] == []
    assert [(row['equal'], row['messages'], row['records_with_counts'], row['declared']['cacheRead']) for row in out['run_totals']] == [(True, 4, 1, 300), (True, 6, 1, 900)]


def split_usage(document, schema):
    """Move 40 input and 5 output tokens of the first run from its last message to its first assistant message."""
    rows = next(table for table in document['stores'][0]['tables'] if table['table'] == 'transcript_events')['rows']
    first = True
    for row in rows:
        if row['values']['event_json'] is None:
            continue
        event = json.loads(row['values']['event_json'])
        message = event.get('message') if isinstance(event, dict) else None
        if not isinstance(message, dict) or message.get('role') != 'assistant' or message['__openclaw']['runId'] != RUNS[0]:
            continue
        usage = message['usage']
        if first:
            usage.update(input=40, output=5)
            first = False
        elif message['__openclaw'].get('runTerminal'):
            usage.update(input=usage['input'] - 40, output=usage['output'] - 5)
        else:
            continue
        row['values'].update(event_json=compact(event), event_utf8_bytes=len(compact(event).encode()))


def test_a_total_split_over_records_with_counts_reconciles_and_a_wrong_total_does_not():
    # Run 2 still holds its whole total in one record, so no reconciliation is stated for the session.
    assert decode(normal_session(), change=split_usage)['reconciliation'] == []

    def split_both(document, schema):
        split_usage(document, schema)
        rows = next(table for table in document['stores'][0]['tables'] if table['table'] == 'transcript_events')['rows']
        first = True
        for row in rows:
            if row['values']['event_json'] is None:
                continue
            event = json.loads(row['values']['event_json'])
            message = event.get('message') if isinstance(event, dict) else None
            if not isinstance(message, dict) or message.get('role') != 'assistant' or message['__openclaw']['runId'] != RUNS[1]:
                continue
            usage = message['usage']
            if first:
                usage.update(input=4)
                first = False
            elif message['__openclaw'].get('runTerminal'):
                usage.update(input=usage['input'] - 4)
            else:
                continue
            row['values'].update(event_json=compact(event), event_utf8_bytes=len(compact(event).encode()))

    out = decode(normal_session(), change=split_both)
    assert out['reconciliation'][0]['matches_session_totals'] is True and [row['records_with_counts'] for row in out['run_totals']] == [2, 2]

    def other_total(document, schema):
        split_both(document, schema)
        rows = next(table for table in document['stores'][0]['tables'] if table['table'] == 'trajectory_runtime_events')['rows']
        for row in rows:
            event = json.loads(row['values']['event_json'])
            if event['type'] == 'model.completed' and event['runId'] == RUNS[1]:
                event['data']['usage']['input'] += 1
                row['values']['event_json'] = compact(event)

    assert decode(normal_session(), change=other_total)['reconciliation'][0]['matches_session_totals'] is False

    def no_total(document, schema):
        split_both(document, schema)
        table = next(table for table in document['stores'][0]['tables'] if table['table'] == 'trajectory_runtime_events')
        table['rows'] = [row for row in table['rows'] if json.loads(row['values']['event_json'])['type'] != 'model.completed']

    assert decode(normal_session(), change=no_total)['reconciliation'] == []


def test_changed_file_from_the_inspect_output_and_the_patch_diff_and_the_final_chain():
    out = decode(normal_session())
    change, = out['file_changes']
    assert (change['path'], change['before_sha256'], change['after_sha256']) == ('fixture_project/checkout.py', sha(BEFORE.encode()), sha(AFTER.encode()))
    assert change['hash_source'] == 'native_preimage_and_edit' and change['action_id'] == change['call_id']
    final = next(row for row in out['relations'] if row['kind'] == 'final_after')
    assert final['from_id'] == out['turns'][1]['id'] and final['native_order_rows'] == sorted(final['native_order_rows'])
    edit = next(row for row in out['results'] if row['action_id'] == change['action_id'])
    # The declared text of the edit result: key-sorted JSON of the result object.
    assert edit['status'] == 'success' and json.loads(edit['output'])['status'] == 'completed' and edit['output'] == result_object_text(edit['output'])
    for broken in (lambda session: session.patch(diff=DIFF.replace('price for price', 'cost for cost')), lambda session: session.patch(error=True)):
        session = Session()
        session.user(PROMPTS[0]); session.shell(helper('inspect'), helper_line('inspect')); session.text('x ' + R1, USAGE[0])
        session.user(PROMPTS[1]); broken(session); session.text('y ' + R2, USAGE[1])
        assert decode(session)['file_changes'] == []


def test_script_form_is_a_second_statement_only_when_it_holds_the_command_and_directory_or_the_changed_lines():
    items = transcript_items(read_rows(*normal_session().export())[0]['transcript_events'])
    calls = {item['name']: item for item in items if item['kind'] == 'call'}
    scripts = [item for item in items if item['kind'] == 'call' and item['name'] == 'exec']
    pairs = pair_script_calls(items)
    assert len(pairs) == 3 and all(target is not None and target.startswith('exec-') for target in pairs.values())
    assert script_restates(scripts[0], calls['bash']) is False     # calls['bash'] is the final helper: another command
    patch_script = next(item for item in scripts if 'apply_patch' in item['arguments']['input'])
    assert script_restates(patch_script, calls['apply_patch']) is True
    other = {**calls['apply_patch'], 'arguments': {'changes': [{'path': WORKDIR + '/checkout.py', 'diff': DIFF.replace('>= 50', '>= 60')}]}}
    assert script_restates(patch_script, other) is False
    assert result_restates(json.dumps([{'type': 'input_text', 'text': 'Output:\n'}, {'type': 'input_text', 'text': 'full output\n'}]), 'full output\n') is True
    assert result_restates(json.dumps([{'type': 'input_text', 'text': 'part'}]), 'full output\n') is False and result_restates('not json', 'x') is False
    assert inner_command("/bin/zsh -lc 'a && b'") == 'a && b' and inner_command('a && b') == 'a && b'

    # A script-form call that names another command is its own action.
    session = Session()
    session.user(PROMPTS[0])
    session.shell(helper('inspect'), helper_line('inspect'), script=False)
    session.exec += 1
    session._script('const r = await tools.exec_command({"cmd":"ls","workdir":"/tmp"});', '[]')
    session.text('x ' + R1, USAGE[0]); session.user(PROMPTS[1]); session.text('y ' + R2, USAGE[1])
    out = decode(session)
    assert [row['name'] for row in out['actions']] == ['bash', 'exec'] and out['status'] == 'ok'


def test_every_event_is_stated_more_than_once_and_the_first_statement_keeps_its_role():
    read = read_rows(*normal_session().export())[0]
    record_ids, roles, statements = read_statements(read)
    occurrences = forward_occurrences(record_ids, statements)
    counts = {}
    for event, _ in occurrences:
        counts[event] = counts.get(event, 0) + 1
    # 2 prompts, 4 texts, 3 calls, 3 results and the script-form result of the patch.
    assert len(counts) == 13 and min(counts.values()) >= 2
    transcript = len(read['transcript_events'])
    alone = {}
    for event, _ in forward_occurrences(record_ids[:transcript], statements[:transcript]):
        alone[event] = alone.get(event, 0) + 1
    assert sum(value == 1 for value in alone.values()) == 8     # without the trace: prompts, texts, the two patch results
    final = roles_after_repeats(roles, statements)
    by_record = dict(zip(record_ids, final))
    assert by_record['transcript_events:0'] == 'session' and by_record['transcript_events:1'] == 'user_message'
    assert final[:transcript].count('snapshot') == 5 and set(final[transcript:]) == {'snapshot', 'metadata', 'session'}
    assert 'unknown' not in final


def test_family_counts_every_row_and_the_outside_files_by_size(tmp_path):
    rows, schema = normal_session().export()
    native = tmp_path / 'native'
    (native / 'capture').mkdir(parents=True)
    (native / 'capture' / ROWS_FILE).write_bytes(rows)
    (native / 'capture' / SCHEMA_FILE).write_bytes(schema)
    artifacts = [{'path': 'capture/' + ROWS_FILE, 'sha256': sha(rows)}, {'path': 'capture/' + SCHEMA_FILE, 'sha256': sha(schema)}]
    outside = [{'relative_path': 'agents/main/agent/codex-home/sessions/rollout-x.jsonl', 'size_bytes': 50000}]
    records, locators, exceptions, occurrences = read_openclaw_family(native, artifacts=artifacts, outside_files=outside)
    assert exceptions == [] and len(records) == len(locators) and len({row['record_id'] for row in records}) == len(records)
    kinds = {}
    for row in records:
        kinds.setdefault(row['record_kind'], []).append(row)
    assert {'user_message', 'assistant_message', 'tool_call', 'tool_result', 'snapshot', 'metadata', 'index', 'session'} == set(kinds)
    # A table outside the read has a fixed unclassified role; the outside file is one record of its size.
    assert [row['logical_bytes'] for row in kinds['metadata'] if row['record_id'].startswith('outside-the-read:')] == [50000]
    assert len(kinds['index']) == 1 and all(row['classification'] == 'unclassified' for row in kinds['index'] + kinds['metadata'] + kinds['snapshot'])
    density = build_openclaw_density(native, artifacts=artifacts, outside_files=outside, complete_record_family=True).evidence
    useful = sum(row['logical_bytes'] for row in density['records'] if row['classification'] == 'useful')
    without = build_openclaw_density(native, artifacts=artifacts, complete_record_family=True).evidence
    assert sum(row['logical_bytes'] for row in density['records']) - sum(row['logical_bytes'] for row in without['records']) == 50000
    assert 0 < useful < sum(row['logical_bytes'] for row in without['records'])
    assert build_openclaw_density(native, artifacts=artifacts, complete_record_family=False).evidence['evidence_complete'] is False
    with pytest.raises(ValueError, match='differs from its inventory'):
        read_openclaw_family(native, artifacts=[{**artifacts[0], 'sha256': '0' * 64}, artifacts[1]])


def _transcript(document):
    return next(table for table in document['stores'][0]['tables'] if table['table'] == 'transcript_events')


def _edit_event(document, seq, edit):
    row = _transcript(document)['rows'][seq]
    event = json.loads(row['values']['event_json'])
    edit(event)
    text = compact(event)
    row['values'].update(event_json=text, event_utf8_bytes=len(text.encode()))


@pytest.mark.parametrize('change, code', [
    (lambda document, schema: _edit_event(document, 0, lambda event: event.update(version=5)), 'unsupported_version'),
    (lambda document, schema: document['stores'][0].update(user_version=25), 'unsupported_version'),
    (lambda document, schema: _edit_event(document, 4, lambda event: event.update(parentId='another')), 'invalid_boundary'),
    (lambda document, schema: _edit_event(document, 2, lambda event: event['message']['content'].append({'type': 'text', 'text': 'x'})), 'malformed_record'),
    (lambda document, schema: _transcript(document)['rows'][3]['values'].update(event_utf8_bytes=1), 'malformed_record'),
    (lambda document, schema: _transcript(document)['rows'][1]['values'].update(event_zstd={'blob_hex': 'c0ffee'}), 'malformed_record'),
    (lambda document, schema: _transcript(document)['columns'].append('extra') or [row['values'].update(extra=None) for row in _transcript(document)['rows']],
     'unknown_column_set'),
    (lambda document, schema: _transcript(document)['rows'].pop(4), 'invalid_boundary'),
])
def test_a_shape_outside_the_contract_makes_the_decode_unsupported(change, code):
    out = decode(normal_session(), change=change)
    assert out['status'] == 'unsupported' and code in {row['code'] for row in out['diagnostics']}, out['diagnostics']


def test_trace_and_transcript_must_agree_about_the_outcome_and_a_bad_document_is_refused():
    def disagree(document, schema):
        rows = next(table for table in document['stores'][0]['tables'] if table['table'] == 'trajectory_runtime_events')['rows']
        for row in rows:
            event = json.loads(row['values']['event_json'])
            if event['type'] == 'tool.result':
                event['data']['isError'] = not event['data']['isError']
                row['values']['event_json'] = compact(event)
                break

    assert 'invalid_boundary' in {row['code'] for row in decode(normal_session(), change=disagree)['diagnostics']}
    rows, schema = normal_session().export()
    with pytest.raises(OpenClawRowsError, match='not one session with the agent store'):
        decode_openclaw_session_rows(rows.replace(ROWS_SCHEMA.encode(), b'another-schema'), schema)
    with pytest.raises(OpenClawRowsError, match='duplicate JSON key'):
        decode_openclaw_session_rows(b'{"stores": [], "stores": []}', schema)


def test_loss_control_removes_the_one_final_response_row_and_the_decode_stays_clean():
    rows, schema = normal_session().export()
    damaged, removed = remove_final_response(rows, R2)
    assert len(removed) == 1 and removed[0]['table'] == 'transcript_events'
    out = decode_openclaw_session_rows(damaged, schema)
    assert out['status'] == 'ok' and [row.get('canary') for row in out['responses'] if row['phase'] == 'final_answer'] == [R1]
    with pytest.raises(ValueError, match='one final response row'):
        remove_final_response(rows, 'SB_SURVIVAL_V1_RESPONSE_ABSENT')


# --- The three real private packets ---

PRIVATE = ROOT / 'artifacts/v1-expanded-preparation/openclaw-score-replay-v6'
REAL_RUNS = ('openclaw-2026-10-07-04', 'openclaw-2026-10-07-07', 'openclaw-2026-10-07-06')
real = pytest.mark.skipif(not (PRIVATE / 'summary.json').is_file(), reason='OpenClaw private packets are not in this checkout')


@real
def test_31_metric_rows_of_each_run():
    summary = json.loads((PRIVATE / 'summary.json').read_bytes())
    assert [row['run_id'] for row in summary['runs']] == list(REAL_RUNS)
    for row in summary['runs']:
        assert row['metric_count'] == row['resolved_metric_count'] == 31 and row['unresolved_metric_ids'] == []
        assert row['os_sandboxed'] is True and row['tamper_controls'] == 'passed' and row['selected_loss_detected'] is True
        metrics = {item['id']: item for item in json.loads((PRIVATE / f"{row['run_id']}-receipt.json").read_bytes())['diagnostics']['intact']['metrics']}
        assert metrics['portable.complete_root']['state'] == 'contradiction'
        assert metrics['broad.naive_reader_duplicate_safety']['correct'] == 0
        density = metrics['broad.classified_content_density']
        assert 0.02 < density['correct'] / density['observed_eligible'] < 0.05
        full = [name for name, item in metrics.items() if item['state'] == 'measured' and item['correct'] == max(item['observed_eligible'], item['decoded_eligible'])]
        assert len(full) == 27 and {'work.results', 'work.changed_files', 'revision.final_after_r2'} <= set(full)
        assert metrics['attribution.reconciliation']['state'] == 'native_absent' and metrics['attribution.reconciliation']['correct'] == 0


@real
@pytest.mark.parametrize('run', REAL_RUNS)
def test_real_rows_decode_under_the_contract(run):
    from session_bench.openclaw_session_rows import decode_openclaw_native
    out = decode_openclaw_native(PRIVATE / run / 'native', run_canary='SB_SURVIVAL_V1_RUN_' + run)
    assert out['status'] == 'ok' and len(out['turns']) == 2 and len(out['file_changes']) == 1 and len(out['usage']) == 2
    assert out['reconciliation'] == [] and [row['equal'] for row in out['run_totals']] == [True, True] and out['harness'] == 'openclaw'
    assert all(row.get('exit_code') in (0, 1) for row in out['results'] if row.get('helper_nonce'))
