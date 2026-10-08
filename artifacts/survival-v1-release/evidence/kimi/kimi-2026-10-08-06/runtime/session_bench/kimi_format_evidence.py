"""Fail-closed broad evidence for the session directory of one Kimi session.

Every value is read from the wire log and the state file of the packet. A
property that these bytes do not hold is reported as absent, never filled in
from the capture context.
"""
import hashlib
import json
from pathlib import Path

from .format_response_population import _bound_observer, build_observer_rationale_evidence, observed_response_text_matches
from .kimi_wire_records import FORMAT, PROTOCOL_VERSION, STATE_VERSION, build_kimi_density, read_kimi_family
from .live_metric_comparator import (
    _field, _match_action, _match_many, _match_response, _match_turn, _native_id, _native_responses, _observer_events,
    _same_path, _validate_observer,
)
from .v1_public_score import FORMAT_EVIDENCE_SCHEMA_VERSION, FORMAT_METRICS, validate_format_evidence

_EVENT_KINDS = ('user_turn', 'assistant_response', 'action', 'result', 'file_change')


def kimi_event_timestamps(decoded, *, observer, run_id, observer_document, complete_record_family):
    """Timestamp of each observed event, read from the wire record of that event.

    A turn, a response and an action use the comparator's match rules. A
    result is the native result of the native action that pairs with the
    observed action, and it gets time credit only when every observed value
    (output, status, exit code, nonce) is in the native result: the same test
    as ``work.results``. The time is ``time`` of the wire record: Unix
    milliseconds, UTC. The changed file has the time of its
    ``file_history.checkpoint`` record.
    """
    document = _bound_observer(observer_document, observer, run_id)
    _, events, _ = _validate_observer(document)
    by_kind = {kind: _observer_events(events, kind) for kind in _EVENT_KINDS}
    required = [row['id'] for row in events if row['kind'] in _EVENT_KINDS and row['population_role'] == 'primary_scored']
    _, _, matched_turns, _ = _match_many(by_kind['user_turn'], decoded['turns'], _match_turn)
    turn_map = {_native_id(candidate): expected for expected, candidate in matched_turns.items()}
    _, _, matched_actions, _ = _match_many(by_kind['action'], decoded['actions'], lambda row, candidate: _match_action(row, candidate, turn_map))
    paired = {candidate['id']: expected for expected, candidate in matched_actions.items()}

    def result_match(row, candidate):
        fields = row['fields']
        if paired.get(candidate.get('action_id')) != fields.get('action_id'):
            return False
        return all(fields.get(key) is None or fields[key] == candidate.get(key) for key in ('output', 'status', 'exit_code', 'helper_nonce'))

    def change_match(row, candidate):
        fields = row['fields']
        return (_same_path(fields.get('path'), candidate.get('path')) and isinstance(candidate.get('before_sha256'), str)
                and all(fields.get(key) == candidate.get(key) for key in ('before_sha256', 'after_sha256')))

    matchers = {
        'user_turn': lambda row, candidate: _match_turn(row, candidate).value is True,
        'assistant_response': lambda row, candidate: (_match_response(row, candidate, turn_map).value is True
                                                      and observed_response_text_matches(row['fields'], _field(candidate, 'text', 'content'))),
        'action': lambda row, candidate: paired.get(candidate['id']) == row['id'],
        'result': result_match, 'file_change': change_match,
    }
    candidates = {'user_turn': decoded['turns'], 'assistant_response': _native_responses(decoded), 'action': decoded['actions'],
                  'result': decoded['results'], 'file_change': decoded['file_changes']}
    records = []
    for kind in _EVENT_KINDS:
        for candidate in candidates[kind]:
            matched = [row['id'] for row in by_kind[kind] if matchers[kind](row, candidate)]
            if not matched:
                continue
            event_id = matched[0] if len(matched) == 1 else f'ambiguous-native-event:{kind}:{len(records)}'
            records.append({'id': event_id, 'timestamp': candidate.get('timestamp'), 'unit': 'unix_ms', 'time_zone': 'UTC'})
    complete = bool(required) and complete_record_family and decoded['status'] == 'ok' and not decoded['diagnostics']
    return {'evidence_complete': complete, 'event_ids': required, 'records': records}


def build_kimi_format_evidence(decoded, *, context, common):
    complete = common.get('complete_record_family', context.get('complete_record_family')) is True and decoded['status'] == 'ok'
    observer = common['observer']; native = common['native_manifest']
    documentation = Path(__file__).resolve().parents[1] / 'docs/survival-v1/adapters/kimi.md'
    docref = {'id': 'documentation:' + FORMAT, 'sha256': hashlib.sha256(documentation.read_bytes()).hexdigest()}
    exceptions, density, density_locators, occurrences = list(decoded['diagnostics']), None, [], []
    package = common.get('native_package')
    if complete and package is not None:
        inventory = json.loads((Path(package) / 'decode.json').read_bytes())['artifacts']
        try:
            _, _, family_exceptions, occurrences = read_kimi_family(package, artifacts=inventory)
            exceptions += [item for item in family_exceptions if item not in exceptions]
            result = build_kimi_density(package, artifacts=inventory, complete_record_family=True)
            if result.evidence['evidence_complete']:
                density, density_locators = result.evidence, list(result.native_locators)
        except (ValueError, OSError, KeyError, TypeError) as error:
            exceptions.append({'code': 'family_unreadable', 'detail': str(error)})
    # The declared version: ``protocol_version`` of the first record of the wire log and ``version`` of the state file.
    values = (decoded.get('protocol_version'), decoded.get('state_version'))
    known = isinstance(values[0], str) and bool(values[0]) and type(values[1]) is int
    declared = f'wire protocol_version={values[0]}; state.json version={values[1]}' if known else ''
    contract = f'{FORMAT}; wire protocol_version={PROTOCOL_VERSION}; state.json version={STATE_VERSION}'
    versions_match = decoded['status'] == 'ok' and known and values == (PROTOCOL_VERSION, STATE_VERSION)
    session = decoded['session_id'] if isinstance(decoded.get('session_id'), str) and decoded['status'] == 'ok' else ''
    decoded_clean = decoded['status'] == 'ok' and not exceptions
    root_rows = context.get('root_repetitions')
    broad = {
      'broad.readable_rationale': build_observer_rationale_evidence(decoded, observer=observer, run_id=context['run_id'], observer_document=common['observer_document'], complete_record_family=complete),
      # Parents are explicit: every loop event names its turn by ``turnId`` and a tool result names its call by ``parentUuid``.
      'broad.thread_structure': {'evidence_complete': complete, 'session_id': session, 'turns': decoded.get('thread', []), 'explicit_parentage': True},
      'broad.standard_tools_readable': {'evidence_complete': True, 'container': 'jsonl', 'parser': 'Python json (standard library): JSON lines and one JSON file', 'vendor_binary_required': False, 'account_required': False, 'backend_required': False, 'network_required': False},
      'broad.documented_format': {'evidence_complete': True, 'document_id': docref['id'], 'mapping': {
          'containers': 'the session directory KIMI_CODE_HOME/sessions/<workspace-id>/<session-id>/: agents/main/wire.jsonl (JSON lines, append-only) and state.json (one JSON object); a log, a notify state and file copies beside them',
          'record_types': 'a wire record has a type: metadata, turn.prompt, context.append_message, context.append_loop_event (step.begin, content.part, tool.call, tool.result, step.end), llm.request, agent.message.appended, file_history.tracked, file_history.checkpoint, turn.ended and bookkeeping types',
          'identities': 'state.json id is the session id; a prompt has a promptId and a turnId; a step and a content part have a uuid; a tool call has a toolCallId and a uuid; a model request has a turnStep',
          'joins': 'a loop event names its turn by turnId and its step by stepUuid; a tool result names its call by toolCallId and parentUuid; llm.request names its step by turnStep (<turnId>.<step>); step.end holds the usage of its step',
          'version_semantics': 'the first wire record holds protocol_version (the version of the wire record format); state.json holds version (the version of the state file); neither is the application build'}},
      # Session id from the state file; harness and surface from the first sentence of the system prompt in ``profile.bind``.
      'broad.self_contained_identity': {'evidence_complete': complete, 'session_id': session,
          'harness': decoded.get('harness') or '', 'surface': decoded.get('surface') or '', 'record_family': decoded['format'],
          'external_lookup_required': False, 'absolute_path_required': False},
      'broad.declared_format_version': {'evidence_complete': complete and known, 'format_version': declared, 'machine_readable': bool(declared),
          'bundle_binding': native['id']},
      'broad.event_timestamps': kimi_event_timestamps(decoded, observer=observer, run_id=context['run_id'],
          observer_document=common['observer_document'], complete_record_family=complete),
      'broad.honest_version_signal': {'evidence_complete': complete and known, 'declared_version': declared, 'decoder_contract_version': contract,
          'incompatible_schema_distinguished': bool(declared), 'matches_decoder_contract': versions_match},
      'broad.observed_schema_stability': {'evidence_complete': complete, 'advertised_contract': decoded['format'],
          'observations': [{'build': context['build'], 'observed_on': context['collected_on'], 'decoder_contract': decoded['format'], 'decoded': decoded_clean}],
          'exceptions': exceptions},
      'broad.stable_root_location': {'evidence_complete': complete and bool(root_rows), 'repetitions': root_rows if complete and root_rows else []},
      'broad.naive_reader_duplicate_safety': {'evidence_complete': complete and bool(occurrences),
          'event_ids': list(dict.fromkeys(identity for identity, _ in occurrences)),
          'forward_records': [{'event_id': identity, 'occurrence_id': occurrence, 'state': 'active'} for identity, occurrence in occurrences],
          'deduplication': {'documented': True, 'rule': 'the read is wire.jsonl in line order, then state.json; a prompt is named by its promptId, an assistant text by the uuid of its text part, a call and its result by the toolCallId; context.append_message with origin.kind user and agent.message.appended state a prompt, a text, a call or a result again when they hold it; no field marks a record as superseded'}},
      'broad.classified_content_density': density or {'evidence_complete': False, 'classification_rule': 'logical-record-role-v1', 'records': []},
    }
    root_locators = common.get('kimi_root_locators') or []
    def locators(metric):
        if metric == 'broad.documented_format': return [docref]
        if metric == 'broad.classified_content_density' and density_locators: return density_locators
        if metric == 'broad.stable_root_location' and root_locators: return root_locators
        return [native]
    result = {'schema_version': FORMAT_EVIDENCE_SCHEMA_VERSION, 'run_id': context['run_id'], 'configuration_id': 'kimi', 'repetition': context['repetition'],
      'build': context['build'], 'collected_on': context['collected_on'], 'result_id': context['result_id'], 'observer': observer, 'native_manifest': native,
      'profile': {'schema_version': 'session-bench-format-profile-v1', 'run_id': context['run_id'], 'configuration_id': 'kimi', 'repetition': context['repetition'], 'broad_evidence': broad},
      'metric_evidence': [{'metric_id': metric, 'observer_ids': [observer['id']], 'native_locators': locators(metric)} for metric in FORMAT_METRICS]}
    validate_format_evidence(result)
    return result
