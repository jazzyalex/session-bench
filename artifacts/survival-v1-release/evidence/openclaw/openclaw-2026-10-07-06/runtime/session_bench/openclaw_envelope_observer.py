"""Independent OpenClaw observer from the ``--json`` stdout envelope of each turn.

``openclaw agent --local --session-id ID --json --message-file FILE`` writes one
JSON object to stdout when the turn ends (OpenClaw 2026.9.8, probes
``openclaw-2026-10-07-probe-04..06``). Its keys:

- ``payloads``: the visible reply, ``[{"text", "mediaUrl"}]``.
- ``meta.agentMeta``: ``sessionId`` (the ``--session-id`` value), ``provider``,
  ``model``, ``agentHarnessId``, ``credentialSource.kind``, ``usage`` and
  ``lastCallUsage`` (``input``, ``output``, ``cacheRead``, ``cacheWrite``,
  ``reasoningTokens``, ``total``), ``terminalReceipt`` (``runId``, ``turnId``,
  requested and effective model, ``successfulToolNames``,
  ``assistantTranscriptIdempotencyKey``).
- ``meta.systemPromptReport``: ``sessionId``, ``sessionKey``, ``workspaceDir``,
  sizes and hashes of the prompt parts.
- ``meta.finalAssistantVisibleText``, ``meta.executionTrace`` (attempts,
  ``fallbackUsed``), ``meta.completion``, ``meta.aborted``, ``meta.stopReason``.
- ``meta.toolSummary``: ``calls`` (a number), ``tools`` (names), ``failures``
  (a number). The key is absent when the turn made no tool call.

A failed run writes another object: ``{"ok": false, "status", "error", ...}``.

What the envelope gives and what it does not:

- The final reply, the session, the provider and model, the token counts of the turn.
- For tools: how many calls, which tool names, how many failed. **No single
  tool call**: no arguments, no result, no exit code, no call id, no order.
  stderr holds four log lines per turn and no tool event.

So this observer has no tool events. Its actions and results come from the
helper ledger (the frozen helper writes it itself) and from the file hashes.
By the ruling of the Hermes review (2026-10-06) that is not enough for
``work.actions``, ``work.results`` and ``causal.action_result``: the observer
names them in ``unobserved_metrics`` and a scorer must keep them unresolved
(``ENVELOPE_UNOBSERVED``, the same rule as Pi print mode).

The observer never reads a native row. Its inputs are the two stdout
envelopes, the helper ledger after each turn, and the SHA-256 of the workload
file before and after.
"""
import json
import re

from .live_metric_comparator import _validate_observer

TARGET = 'fixture_project/checkout.py'
ENVELOPE_UNOBSERVED = ('work.actions', 'work.results', 'causal.action_result')
_USAGE_KEYS = ('input', 'output', 'cacheRead', 'cacheWrite')
_UUID = r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'
_TRANSCRIPT_KEY = re.compile(rf'codex-app-server:({_UUID}):({_UUID}):assistant')
LIMITS = (
    'The envelope reports tool calls only as a count, a list of names and a failure count per turn.',
    'No tool call arguments, results, exit codes, call ids or order are observed.',
    'Helper actions and results come from the helper ledger: it proves that the process ran, not that the harness showed the call or the result.',
    'The edit action comes from the file hashes: they prove the edit boundary, not a tool call.',
)


class OpenClawEnvelopeError(ValueError):
    """The stdout of a turn is not the envelope of one completed turn, or it is ambiguous."""


def _strict(raw, label):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('duplicate key')
            result[key] = value
        return result

    try:
        return json.loads(raw.decode('utf-8'), object_pairs_hook=pairs)
    except (UnicodeDecodeError, ValueError) as error:
        raise OpenClawEnvelopeError(f'{label} is not one JSON object') from error


def _count(value):
    return type(value) is int and value >= 0


def thread_id_from_receipt(receipt):
    """The Codex thread id inside ``assistantTranscriptIdempotencyKey``, or None.

    The key is ``codex-app-server:<thread id>:<turn id>:assistant``. The turn id
    must equal ``turnId`` of the same receipt; otherwise the key is not understood.
    """
    key = receipt.get('assistantTranscriptIdempotencyKey') if isinstance(receipt, dict) else None
    match = _TRANSCRIPT_KEY.fullmatch(key) if isinstance(key, str) else None
    if match is None or match.group(2) != receipt.get('turnId') or match.group(1) == match.group(2):
        return None
    return match.group(1)


def envelope_summary(raw, label='stdout'):
    """The facts of one stdout envelope; raises unless it is one completed turn without fallback."""
    document = _strict(raw, label)
    if isinstance(document, dict) and document.get('ok') is False:
        raise OpenClawEnvelopeError(f"{label} reports a failed run (status {document.get('status')!r})")
    if not isinstance(document, dict) or set(document) != {'payloads', 'meta'} or not isinstance(document['meta'], dict):
        raise OpenClawEnvelopeError(f'{label} is not an agent envelope with payloads and meta')
    payloads, meta = document['payloads'], document['meta']
    agent, report, trace = meta.get('agentMeta'), meta.get('systemPromptReport'), meta.get('executionTrace')
    if not isinstance(agent, dict) or not isinstance(report, dict) or not isinstance(trace, dict):
        raise OpenClawEnvelopeError(f'{label} lacks agentMeta, systemPromptReport or executionTrace')
    # One reply. More payloads may be tool notes of a verbose session: this observer does not know them.
    if (not isinstance(payloads, list) or len(payloads) != 1 or not isinstance(payloads[0], dict)
            or not isinstance(payloads[0].get('text'), str) or payloads[0]['text'] != meta.get('finalAssistantVisibleText')):
        raise OpenClawEnvelopeError(f'{label} does not hold exactly one reply equal to the final visible text')
    session = agent.get('sessionId')
    if not isinstance(session, str) or not session or report.get('sessionId') != session:
        raise OpenClawEnvelopeError(f'{label} does not name one session')
    if meta.get('aborted') is not False or meta.get('stopReason') != 'stop':
        raise OpenClawEnvelopeError(f'{label} reports a turn that did not complete')
    provider, model = agent.get('provider'), agent.get('model')
    if (not isinstance(provider, str) or not provider or not isinstance(model, str) or not model
            or trace.get('fallbackUsed') is not False or trace.get('winnerProvider') != provider or trace.get('winnerModel') != model):
        raise OpenClawEnvelopeError(f'{label} does not confirm one provider and model without fallback')
    receipt = agent.get('terminalReceipt') if isinstance(agent.get('terminalReceipt'), dict) else {}
    if receipt.get('rerouted') not in (None, False) or receipt.get('sessionId') not in (None, session):
        raise OpenClawEnvelopeError(f'{label} reports a rerouted turn or another session in its receipt')
    usage = agent.get('usage')
    if not isinstance(usage, dict) or not all(_count(usage.get(key)) for key in _USAGE_KEYS):
        raise OpenClawEnvelopeError(f'{label} lacks the token counts of the turn')
    tools = meta.get('toolSummary', {'calls': 0, 'tools': [], 'failures': 0})
    if (not isinstance(tools, dict) or not _count(tools.get('calls')) or not _count(tools.get('failures'))
            or not isinstance(tools.get('tools'), list) or not all(isinstance(name, str) for name in tools['tools'])
            or bool(tools['calls']) != bool(tools['tools']) or tools['failures'] > tools['calls']):
        raise OpenClawEnvelopeError(f'{label} holds a tool summary that is not understood')
    key = report.get('sessionKey')
    stamp = report.get('generatedAt')
    return {'session_id': session, 'session_key': key if isinstance(key, str) and key else None,
            'workspace_dir': report.get('workspaceDir') if isinstance(report.get('workspaceDir'), str) else None,
            'provider': provider, 'model': model, 'harness': agent.get('agentHarnessId'),
            'credential_source': (agent.get('credentialSource') or {}).get('kind') if isinstance(agent.get('credentialSource'), dict) else None,
            'text': payloads[0]['text'], 'usage': {key: usage[key] for key in sorted(usage) if _count(usage[key])},
            'tool_summary': {'calls': tools['calls'], 'tools': sorted(tools['tools']), 'failures': tools['failures']},
            'run_id': receipt.get('runId'), 'turn_id': receipt.get('turnId'), 'thread_id': thread_id_from_receipt(receipt),
            'duration_ms': meta.get('durationMs') if _count(meta.get('durationMs')) else None,
            'observed_at': stamp if _count(stamp) else None}


def _ledger(raw, label):
    try:
        rows = [json.loads(line) for line in raw.decode('utf-8').splitlines() if line.strip()]
    except (UnicodeDecodeError, ValueError) as error:
        raise OpenClawEnvelopeError(f'{label} is not JSON lines') from error
    if not all(isinstance(row, dict) for row in rows):
        raise OpenClawEnvelopeError(f'{label} holds a row that is not an object')
    return rows


def build_openclaw_envelope_observer(*, workload, stdout_by_turn, helper_by_turn, before_sha256, after_sha256):
    """Build the observer from the two envelopes, the helper ledger after each turn and the file hashes.

    The ledger after turn 1 must hold ``inspect`` and ``baseline``; the ledger
    after turn 2 must hold the same two rows and then ``final``. That gives the
    turn of each helper. Raises ``OpenClawEnvelopeError`` when an envelope is
    not a completed turn, when the two turns differ in session, provider or
    model, when a turn with a helper reports no tool call, or when the
    workload file did not change.
    """
    first, last = _ledger(helper_by_turn[1], 'helper ledger R1'), _ledger(helper_by_turn[2], 'helper ledger R2')
    if [row.get('phase') for row in last] != ['inspect', 'baseline', 'final'] or last[:2] != first:
        raise OpenClawEnvelopeError('helper population incomplete, or the ledger of turn 1 is not the start of the ledger of turn 2')
    if before_sha256 == after_sha256:
        raise OpenClawEnvelopeError('the workload file did not change: no observed edit')
    summaries = {number: envelope_summary(stdout_by_turn[number], f'stdout R{number}') for number in (1, 2)}
    for key in ('session_id', 'provider', 'model', 'harness'):
        if summaries[1][key] != summaries[2][key]:
            raise OpenClawEnvelopeError(f'the two envelopes differ in {key}')
    session = summaries[1]['session_id']
    events, relations, emitted = [], [], {}

    def add(identity, kind, fields, metrics, source, *, role='primary_scored', observed_at=None):
        row = {'id': identity, 'sequence': len(events) + 1, 'kind': kind, 'fields': fields, 'metric_ids': metrics,
               'boundary': 'captured', 'population_role': role, 'source': source, 'session_id': session}
        if observed_at is not None:
            row['observed_at'] = observed_at
        events.append(row)
        return identity

    def relate(identity, kind, source, target):
        relations.append({'id': identity, 'kind': kind, 'from_id': source, 'to_id': target, 'sequence': len(relations) + 1})

    def helper(row, turn_id):
        phase = row['phase']
        action = emitted[phase] = add('action-' + phase, 'action', {
            'name': 'shell', 'argv': row['argv'], 'cwd': row.get('cwd'), 'turn_id': turn_id,
            'action_kind': 'inspect' if phase == 'inspect' else 'test', 'tool_event_observed': False},
            ['work.actions', 'causal.action_result'], 'helper_ledger')
        add('result-' + phase, 'result', {'action_id': action, 'turn_id': turn_id, 'helper_nonce': row['helper_nonce'], 'output': row['output'],
                                          'exit_code': row['exit_code'], 'status': 'failure' if row['exit_code'] else 'success',
                                          'tool_event_observed': False},
            ['work.results', 'causal.action_result'], 'helper_ledger')
        relate(action + ':relation', 'action_result', action, 'result-' + phase)

    turn_ids = {}
    for number in (1, 2):
        summary, turn = summaries[number], workload['turns'][number - 1]
        turn_id = turn_ids[number] = turn['id']
        # A turn in which a helper ran, or the file changed, made at least one tool call.
        if summary['tool_summary']['calls'] < 1:
            raise OpenClawEnvelopeError(f'stdout R{number} reports no tool call in a turn where the helper ran')
        add(turn_id, 'user_turn', {'text': turn['text'], 'role': 'user', 'turn_id': turn_id, 'revision': f'r{number}',
                                   'run_canary': workload['run_canary']},
            ['work.submitted_turns', f'revision.r{number}', 'revision.r1_r2_order'], 'submitted_input', observed_at=summary['observed_at'])
        if number == 1:
            for row in last[:2]:
                helper(row, turn_id)
        else:
            emitted['edit'] = add('action-edit', 'action', {'name': 'file_edit', 'target': TARGET, 'action_kind': 'edit', 'turn_id': turn_id,
                                                            'tool_event_observed': False},
                                  ['work.actions', 'causal.action_result'], 'filesystem_observer')
            add('result-edit', 'result', {'action_id': 'action-edit', 'turn_id': turn_id, 'status': 'success', 'tool_event_observed': False},
                ['work.results', 'causal.action_result'], 'filesystem_observer')
            relate('action-edit:relation', 'action_result', 'action-edit', 'result-edit')
            helper(last[2], turn_id)
        canary = turn['response_canary']
        if not summary['text'].rstrip().endswith(canary) or summary['text'].count(canary) != 1:
            raise OpenClawEnvelopeError(f'stdout R{number} final response lacks the exact canary')
        response = f'response-r{number}'
        add(response, 'assistant_response', {
            'text': summary['text'], 'canary': canary, 'turn_id': turn_id, 'role': 'assistant', 'status': 'completed',
            'model_id': summary['model'], 'configuration': {'provider': summary['provider'], 'harness': summary['harness']},
            'usage': summary['usage']},
            ['work.visible_responses', 'causal.turn_response', 'attribution.model_config', 'attribution.usage', 'attribution.token_semantics'],
            'harness_stdout')
        relate(f'relation-turn-{number}', 'turn_response', turn_id, response)
    for row in last:
        phase = row['phase']
        add('helper-' + phase, 'helper', {'phase': phase, 'action_id': emitted[phase], 'helper_nonce': row['helper_nonce'],
                                          'argv': row['argv'], 'exit_code': row['exit_code'], 'output': row['output']},
            [], 'helper_ledger', role='supporting')
        relate('relation-helper-' + phase, 'helper_for', 'helper-' + phase, emitted[phase])
    add('change-checkout', 'file_change', {'path': TARGET, 'before_sha256': before_sha256, 'after_sha256': after_sha256,
                                           'turn_id': turn_ids[2], 'action_id': emitted['edit']}, ['work.changed_files'], 'filesystem_observer')
    relate('relation-final-after-r2', 'final_after', turn_ids[2], emitted['final'])
    relate('relation-r1-r2', 'supersedes', turn_ids[1], turn_ids[2])
    observer = {'schema_version': '1.0-survival-observer', 'protocol_version': '1.0-survival', 'scenario_id': 'survival-v1-repair',
                'run_id': workload['run_id'], 'independent': True,
                'method': ('exact submitted prompts; the --json stdout envelope of each turn (final reply, session, model, token counts, '
                           'tool summary); helper ledger; filesystem hashes; no tool call stream exists in the envelope; no native input'),
                'tool_events_observed': False, 'unobserved_metrics': list(ENVELOPE_UNOBSERVED), 'limits': list(LIMITS),
                'tool_summary_by_turn': {f'r{number}': summaries[number]['tool_summary'] for number in (1, 2)},
                'events': events, 'relations': relations}
    _validate_observer(observer)
    return observer
