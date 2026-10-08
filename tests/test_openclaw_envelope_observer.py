"""OpenClaw envelope observer: what the ``--json`` stdout gives, and that it holds no tool events."""
import json

import pytest

from session_bench.openclaw_envelope_observer import (
    ENVELOPE_UNOBSERVED, OpenClawEnvelopeError, build_openclaw_envelope_observer, envelope_summary, thread_id_from_receipt,
)

SID = '9f1c2d3e-0abd-4117-af54-3aade9c80001'
THREAD = '01a117b7-6ec3-73a3-a113-b1bffc1b0001'
TURNS = {1: '01a117b7-6f9c-7172-9bf0-d47008bd0001', 2: '01a117b8-300d-7342-92ef-221309f60002'}
RUN = 'SB_SURVIVAL_V1_RUN_openclaw-test'
R1, R2 = 'SB_SURVIVAL_V1_RESPONSE_R1_cafe', 'SB_SURVIVAL_V1_RESPONSE_R2_fix'
WORKLOAD = {'run_id': 'openclaw-test', 'run_canary': RUN,
            'turns': [{'id': 'turn-r1', 'text': 'Requirement R1 ' + RUN, 'response_canary': R1},
                      {'id': 'turn-r2', 'text': 'Correction R2 ' + RUN, 'response_canary': R2}]}
EXIT = {'inspect': 0, 'baseline': 1, 'final': 0}
ROWS = [{'phase': phase, 'helper_nonce': f'{phase}-fixture-0001', 'argv': ['python3', 'bench_check.py', phase], 'cwd': 'fixture_project',
         'exit_code': EXIT[phase], 'output': f'SB_SURVIVAL_V1_HELPER_{phase.upper()}_{phase}-fixture-0001 {{}}'} for phase in EXIT]


def ledger(rows):
    return ''.join(json.dumps(row) + '\n' for row in rows).encode()


LEDGERS = {1: ledger(ROWS[:2]), 2: ledger(ROWS)}


def envelope(turn, text, *, session=SID, model='gpt-5.6-terra', tools=('bash',), calls=2, failures=0, change=None):
    """The stdout object of one turn, with the keys of OpenClaw 2026.9.8 that the observer uses."""
    document = {
        'payloads': [{'text': text, 'mediaUrl': None}],
        'meta': {
            'durationMs': 15284,
            'agentMeta': {
                'sessionId': session, 'provider': 'openai', 'model': model, 'agentHarnessId': 'codex', 'credentialSource': {'kind': 'profile'},
                'usage': {'input': 28518, 'output': 307, 'cacheRead': 52736, 'cacheWrite': 0, 'reasoningTokens': 63, 'total': 81561},
                'terminalReceipt': {'runId': 'run-' + str(turn), 'sessionId': session, 'turnId': TURNS[turn], 'rerouted': False,
                                    'successfulToolNames': list(tools),
                                    'assistantTranscriptIdempotencyKey': f'codex-app-server:{THREAD}:{TURNS[turn]}:assistant'}},
            'aborted': False,
            'systemPromptReport': {'generatedAt': 1791399260006 + turn, 'sessionId': session, 'sessionKey': 'agent:main:explicit:' + session,
                                   'workspaceDir': '/synthetic/workspace'},
            'finalAssistantVisibleText': text, 'stopReason': 'stop',
            'executionTrace': {'winnerProvider': 'openai', 'winnerModel': model, 'fallbackUsed': False, 'runner': 'embedded'},
            'toolSummary': {'calls': calls, 'tools': list(tools), 'failures': failures}}}
    if change is not None:
        change(document)
    return json.dumps(document, ensure_ascii=False, indent=2).encode()


def observe(first, second, **options):
    options.setdefault('helper_by_turn', LEDGERS)
    options.setdefault('before_sha256', 'a' * 64)
    options.setdefault('after_sha256', 'b' * 64)
    return build_openclaw_envelope_observer(workload=WORKLOAD, stdout_by_turn={1: first, 2: second}, **options)


def normal_run():
    return envelope(1, 'Baseline fails. ' + R1, failures=1), envelope(2, 'Fixed. ' + R2, tools=('apply_patch', 'bash'))


def test_summary_reads_session_model_usage_tool_counts_and_the_codex_thread():
    summary = envelope_summary(normal_run()[0])
    assert summary['session_id'] == SID and summary['session_key'] == 'agent:main:explicit:' + SID
    assert (summary['provider'], summary['model'], summary['harness'], summary['credential_source']) == ('openai', 'gpt-5.6-terra', 'codex', 'profile')
    assert summary['usage'] == {'input': 28518, 'output': 307, 'cacheRead': 52736, 'cacheWrite': 0, 'reasoningTokens': 63, 'total': 81561}
    assert summary['tool_summary'] == {'calls': 2, 'tools': ['bash'], 'failures': 1}
    assert summary['thread_id'] == THREAD and summary['turn_id'] == TURNS[1] and summary['workspace_dir'] == '/synthetic/workspace'


def test_thread_id_needs_the_turn_id_of_the_same_receipt():
    key = f'codex-app-server:{THREAD}:{TURNS[1]}:assistant'
    assert thread_id_from_receipt({'assistantTranscriptIdempotencyKey': key, 'turnId': TURNS[1]}) == THREAD
    assert thread_id_from_receipt({'assistantTranscriptIdempotencyKey': key, 'turnId': TURNS[2]}) is None
    assert thread_id_from_receipt({'assistantTranscriptIdempotencyKey': 'other:' + key, 'turnId': TURNS[1]}) is None
    assert thread_id_from_receipt({}) is None and thread_id_from_receipt(None) is None


def test_a_turn_without_tool_summary_has_zero_calls():
    summary = envelope_summary(envelope(1, 'No tools. ' + R1, change=lambda doc: doc['meta'].pop('toolSummary')))
    assert summary['tool_summary'] == {'calls': 0, 'tools': [], 'failures': 0}


@pytest.mark.parametrize('change, message', [
    (lambda doc: doc['meta'].update(aborted=True), 'did not complete'),
    (lambda doc: doc['meta'].update(stopReason='error'), 'did not complete'),
    (lambda doc: doc['meta']['executionTrace'].update(fallbackUsed=True), 'without fallback'),
    (lambda doc: doc['meta']['executionTrace'].update(winnerModel='another-model'), 'without fallback'),
    (lambda doc: doc['meta']['agentMeta']['terminalReceipt'].update(rerouted=True), 'rerouted'),
    (lambda doc: doc['meta']['systemPromptReport'].update(sessionId='another-session'), 'one session'),
    (lambda doc: doc['payloads'].append({'text': 'tool note', 'mediaUrl': None}), 'exactly one reply'),
    (lambda doc: doc['payloads'][0].update(text='another text'), 'exactly one reply'),
    (lambda doc: doc['meta']['agentMeta']['usage'].pop('cacheWrite'), 'token counts'),
    (lambda doc: doc['meta']['toolSummary'].update(calls='2'), 'tool summary'),
    (lambda doc: doc['meta']['toolSummary'].update(tools=[]), 'tool summary'),
    (lambda doc: doc.update(extra=1), 'not an agent envelope'),
])
def test_an_envelope_that_is_not_one_completed_turn_fails_closed(change, message):
    with pytest.raises(OpenClawEnvelopeError, match=message):
        envelope_summary(envelope(1, 'Text ' + R1, change=change))


def test_error_object_duplicate_keys_and_other_text_fail_closed():
    failed = json.dumps({'ok': False, 'status': 'error', 'final': '', 'payloads': [], 'model': None, 'provider': None, 'sessionId': SID,
                         'error': {'message': 'unexpected status 401 Unauthorized', 'kind': 'exception'}}).encode()
    with pytest.raises(OpenClawEnvelopeError, match="failed run \\(status 'error'\\)"):
        envelope_summary(failed)
    with pytest.raises(OpenClawEnvelopeError, match='not one JSON object'):
        envelope_summary(b'{"payloads": [], "payloads": [], "meta": {}}')
    with pytest.raises(OpenClawEnvelopeError, match='not one JSON object'):
        envelope_summary(b'log line\n{"payloads": [], "meta": {}}')


def test_observer_holds_turns_responses_helpers_and_the_change_and_declares_that_it_saw_no_tool_events():
    observer = observe(*normal_run())
    assert observer['independent'] is True and observer['tool_events_observed'] is False
    assert observer['unobserved_metrics'] == list(ENVELOPE_UNOBSERVED) == ['work.actions', 'work.results', 'causal.action_result']
    assert observer['tool_summary_by_turn'] == {'r1': {'calls': 2, 'tools': ['bash'], 'failures': 1},
                                                'r2': {'calls': 2, 'tools': ['apply_patch', 'bash'], 'failures': 0}}
    assert 'no native input' in observer['method'] and any('arguments' in limit for limit in observer['limits'])
    events = {row['id']: row for row in observer['events']}
    assert all(row['session_id'] == SID for row in events.values())
    actions = [row for row in observer['events'] if row['kind'] == 'action']
    assert [row['id'] for row in actions] == ['action-inspect', 'action-baseline', 'action-edit', 'action-final']
    # No action or result comes from a tool event: the sources are the helper ledger and the file hashes.
    assert {row['source'] for row in actions} == {'helper_ledger', 'filesystem_observer'}
    assert all(row['fields']['tool_event_observed'] is False for row in observer['events'] if row['kind'] in ('action', 'result'))
    assert [events[name]['fields']['turn_id'] for name in ('action-inspect', 'action-baseline', 'action-edit', 'action-final')] == [
        'turn-r1', 'turn-r1', 'turn-r2', 'turn-r2']
    assert events['result-baseline']['fields']['exit_code'] == 1 and events['result-baseline']['fields']['status'] == 'failure'
    assert events['action-final']['fields']['argv'] == ['python3', 'bench_check.py', 'final']
    response = events['response-r2']
    assert response['source'] == 'harness_stdout' and response['fields']['model_id'] == 'gpt-5.6-terra'
    assert response['fields']['configuration'] == {'provider': 'openai', 'harness': 'codex'} and response['fields']['usage']['cacheWrite'] == 0
    assert events['turn-r1']['fields']['text'] == WORKLOAD['turns'][0]['text'] and events['turn-r1']['observed_at'] == 1791399260007
    assert events['change-checkout']['fields'] == {'path': 'fixture_project/checkout.py', 'before_sha256': 'a' * 64, 'after_sha256': 'b' * 64,
                                                   'turn_id': 'turn-r2', 'action_id': 'action-edit'}
    kinds = [row['kind'] for row in observer['relations']]
    assert kinds.count('action_result') == 4 and kinds.count('turn_response') == 2 and 'final_after' in kinds and 'supersedes' in kinds


def test_observer_fails_closed_on_another_session_model_missing_canary_or_missing_work():
    first, second = normal_run()
    with pytest.raises(OpenClawEnvelopeError, match='differ in session_id'):
        observe(first, envelope(2, 'Fixed. ' + R2, session='0a0a0a0a-0abd-4117-af54-3aade9c80009'))
    with pytest.raises(OpenClawEnvelopeError, match='differ in model'):
        observe(first, envelope(2, 'Fixed. ' + R2, model='gpt-5.4'))
    with pytest.raises(OpenClawEnvelopeError, match='lacks the exact canary'):
        observe(first, envelope(2, f'{R2} and more text'))
    with pytest.raises(OpenClawEnvelopeError, match='lacks the exact canary'):
        observe(envelope(1, f'{R1} twice {R1}'), second)
    with pytest.raises(OpenClawEnvelopeError, match='no tool call'):
        observe(first, envelope(2, 'Fixed. ' + R2, tools=(), calls=0))
    with pytest.raises(OpenClawEnvelopeError, match='no observed edit'):
        observe(first, second, after_sha256='a' * 64)
    with pytest.raises(OpenClawEnvelopeError, match='helper population incomplete'):
        observe(first, second, helper_by_turn={1: LEDGERS[1], 2: ledger(ROWS[:2])})
    # The ledger of turn 1 must be the start of the ledger of turn 2: a helper cannot move to another turn.
    with pytest.raises(OpenClawEnvelopeError, match='not the start'):
        observe(first, second, helper_by_turn={1: ledger(ROWS[:1]), 2: LEDGERS[2]})
    with pytest.raises(OpenClawEnvelopeError, match='not JSON lines'):
        observe(first, second, helper_by_turn={1: b'not json\n', 2: LEDGERS[2]})


def test_the_probe_envelopes_of_2026_10_07_have_no_single_tool_call():
    """What probe 06 proves: a count and names per turn, and nothing else about tools."""
    from pathlib import Path
    probe = Path(__file__).resolve().parents[1] / 'artifacts/v1-expanded-preparation/live-captures/openclaw-2026-10-07-probe-06'
    if not probe.is_dir():
        pytest.skip('the private probe directory is not in this checkout')
    for name, tools in (('r1.stdout.json', ['bash']), ('r2.stdout.json', ['apply_patch', 'bash'])):
        raw = (probe / name).read_bytes()
        summary = envelope_summary(raw, name)
        assert summary['tool_summary']['tools'] == tools and summary['tool_summary']['calls'] == 2
        assert summary['session_id'] == (probe / 'session-id').read_text().strip() and summary['thread_id'] is not None
        document = json.loads(raw)
        text = json.dumps({key: value for key, value in document['meta'].items() if key != 'systemPromptReport'})
        assert 'bench_check.py' not in text and 'arguments' not in text and 'exit_code' not in text and 'exitCode' not in text
