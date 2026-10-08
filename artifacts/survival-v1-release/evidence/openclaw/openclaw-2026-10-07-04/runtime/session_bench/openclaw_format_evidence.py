"""Fail-closed broad evidence for the family of one OpenClaw session.

Every value is read from the row export and the schema export of the packet.
A property that these bytes do not hold is reported as absent, never filled
in from the capture context.
"""
import hashlib
import json
from pathlib import Path

from .format_response_population import _bound_observer, build_observer_rationale_evidence, observed_response_text_matches
from .live_metric_comparator import (
    _field, _match_action, _match_many, _match_response, _match_turn, _native_id, _native_responses, _observer_events,
    _same_path, _validate_observer,
)
from .openclaw_session_rows import (
    FORMAT, TRACE_SCHEMA, TRACE_VERSION, TRANSCRIPT_VERSION, USER_VERSION, build_openclaw_density, read_openclaw_family,
)
from .v1_public_score import FORMAT_EVIDENCE_SCHEMA_VERSION, FORMAT_METRICS, validate_format_evidence

_EVENT_KINDS = ('user_turn', 'assistant_response', 'action', 'result', 'file_change')


def openclaw_event_timestamps(decoded, projected, *, observer, run_id, observer_document, complete_record_family):
    """Timestamp of each observed event, read from the transcript row of that event.

    A turn, a response and an action use the comparator's match rules. A
    result is the native result of the native action that pairs with the
    observed action, and it gets time credit only when every observed value
    (output, status, exit code, nonce) is in the native result: the same test
    as ``work.results``. The time is the ``timestamp`` of the transcript
    event: RFC 3339 with a ``Z`` (UTC). A tool call has the time of its
    assistant row, a result the time of its tool result row, and the changed
    file the time of the result row of its ``apply_patch`` call.
    """
    document = _bound_observer(observer_document, observer, run_id)
    _, events, _ = _validate_observer(document)
    by_kind = {kind: _observer_events(events, kind) for kind in _EVENT_KINDS}
    required = [row['id'] for row in events if row['kind'] in _EVENT_KINDS and row['population_role'] == 'primary_scored']
    _, _, matched_turns, _ = _match_many(by_kind['user_turn'], decoded['turns'], _match_turn)
    turn_map = {_native_id(candidate): expected for expected, candidate in matched_turns.items()}
    _, _, matched_actions, _ = _match_many(by_kind['action'], projected['actions'], lambda row, candidate: _match_action(row, candidate, turn_map))
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
    candidates = {'user_turn': decoded['turns'], 'assistant_response': _native_responses(decoded), 'action': projected['actions'],
                  'result': projected['results'], 'file_change': decoded['file_changes']}
    records = []
    for kind in _EVENT_KINDS:
        for candidate in candidates[kind]:
            matched = [row['id'] for row in by_kind[kind] if matchers[kind](row, candidate)]
            if not matched:
                continue
            event_id = matched[0] if len(matched) == 1 else f'ambiguous-native-event:{kind}:{len(records)}'
            records.append({'id': event_id, 'timestamp': candidate.get('timestamp'), 'unit': 'rfc3339', 'time_zone': 'UTC'})
    complete = bool(required) and complete_record_family and decoded['status'] == 'ok' and not decoded['diagnostics']
    return {'evidence_complete': complete, 'event_ids': required, 'records': records}


def build_openclaw_format_evidence(decoded, projected, *, context, common):
    complete = common.get('complete_record_family', context.get('complete_record_family')) is True and decoded['status'] == 'ok'
    observer = common['observer']; native = common['native_manifest']
    documentation = Path(__file__).resolve().parents[1] / 'docs/survival-v1/adapters/openclaw.md'
    docref = {'id': 'documentation:' + FORMAT, 'sha256': hashlib.sha256(documentation.read_bytes()).hexdigest()}
    exceptions, density, density_locators, occurrences = list(decoded['diagnostics']), None, [], []
    package = common.get('native_package')
    outside = common.get('openclaw_outside_files') or []
    if complete and package is not None:
        inventory = json.loads((Path(package) / 'decode.json').read_bytes())['artifacts']
        try:
            _, _, family_exceptions, occurrences = read_openclaw_family(package, artifacts=inventory, outside_files=outside)
            exceptions += [item for item in family_exceptions if item not in exceptions]
            result = build_openclaw_density(package, artifacts=inventory, outside_files=outside, complete_record_family=True)
            if result.evidence['evidence_complete']:
                density, density_locators = result.evidence, list(result.native_locators)
        except (ValueError, OSError, KeyError, TypeError) as error:
            exceptions.append({'code': 'family_unreadable', 'detail': str(error)})
    # The declared version: the ``version`` of the session header of the transcript (in the record itself), the
    # ``schemaVersion`` of the trace rows, and ``PRAGMA user_version`` of the agent store (in the bound schema export).
    values = (decoded.get('transcript_version'), decoded.get('trace_version'), decoded.get('user_version'))
    known = all(type(value) is int for value in values) and decoded.get('trace_schema') == TRACE_SCHEMA
    declared = (f'transcript session version={values[0]}; {decoded.get("trace_schema")} schemaVersion={values[1]}; '
                f'openclaw-agent.sqlite user_version={values[2]}') if known else ''
    contract = (f'{FORMAT}; transcript session version={TRANSCRIPT_VERSION}; {TRACE_SCHEMA} schemaVersion={TRACE_VERSION}; '
                f'openclaw-agent.sqlite user_version={USER_VERSION}')
    versions_match = decoded['status'] == 'ok' and known and values == (TRANSCRIPT_VERSION, TRACE_VERSION, USER_VERSION)
    session = decoded['session_id'] if isinstance(decoded.get('session_id'), str) and decoded['status'] == 'ok' else ''
    decoded_clean = decoded['status'] == 'ok' and not exceptions
    root_rows = context.get('root_repetitions')
    broad = {
      'broad.readable_rationale': build_observer_rationale_evidence(decoded, observer=observer, run_id=context['run_id'], observer_document=common['observer_document'], complete_record_family=complete),
      # Parents are explicit: every transcript message names the message before it by ``parentId``.
      'broad.thread_structure': {'evidence_complete': complete, 'session_id': session, 'turns': decoded.get('thread', []), 'explicit_parentage': True},
      # The container is SQLite. Every value that a fact needs is an INTEGER or TEXT (JSON text), except a
      # transcript event above a size limit: it is Zstandard-compressed JSON in a BLOB (in these runs the first prompt).
      'broad.standard_tools_readable': {'evidence_complete': True, 'container': 'sqlite', 'parser': 'Python sqlite3, json and compression.zstd (standard library)', 'vendor_binary_required': False, 'account_required': False, 'backend_required': False, 'network_required': False},
      'broad.documented_format': {'evidence_complete': True, 'document_id': docref['id'], 'mapping': {
          'containers': 'the shared agent store ~/.openclaw/agents/main/agent/openclaw-agent.sqlite (SQLite); the session is its rows in the tables transcript_events, trajectory_runtime_events and session_windows; the capture binds these rows as a JSON row export with the schema of the store',
          'record_types': 'a transcript row: the session header, or a message with role user, assistant (one text or one toolCall) or toolResult; a trace row with a type (session.started, context.compiled, prompt.submitted, tool.call, tool.result, model.completed, session.ended); the session_windows row',
          'identities': 'session_windows.session_id is the session id; a transcript message has an id; a tool call has an id and its result names it in toolCallId; a run has a runId on the transcript messages and the trace rows',
          'joins': 'transcript_events.session_id and trajectory_runtime_events.session_id reference session_windows.session_id (declared foreign keys); a transcript message names the message before it by parentId; a tool result names its call by toolCallId; a trace row names its call by data.toolCallId and its run by runId',
          'version_semantics': 'the session header of the transcript holds version; a trace row holds traceSchema and schemaVersion; PRAGMA user_version is the schema version of the store; none is the application build'}},
      # Session id from the session row; harness from the schema name of the trace rows; surface from the session row.
      'broad.self_contained_identity': {'evidence_complete': complete, 'session_id': session,
          'harness': decoded.get('harness') or '', 'surface': decoded.get('surface') or '', 'record_family': decoded['format'],
          'external_lookup_required': False, 'absolute_path_required': False},
      'broad.declared_format_version': {'evidence_complete': complete and known, 'format_version': declared, 'machine_readable': bool(declared),
          'bundle_binding': native['id']},
      'broad.event_timestamps': openclaw_event_timestamps(decoded, projected, observer=observer, run_id=context['run_id'],
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
          'deduplication': {'documented': True, 'rule': 'the read is the rows of the session in transcript_events (by seq), then trajectory_runtime_events (by seq), then session_windows; a prompt and a response are named by the message id, a call and its result by the call id of the execution form; a script-form call or result that holds the command and directory, the changed lines, or the full output of an execution-form call is one more statement of it; a trace row states the events of its run that it holds; no field marks a row as superseded'}},
      'broad.classified_content_density': density or {'evidence_complete': False, 'classification_rule': 'logical-record-role-v1', 'records': []},
    }
    root_locators = common.get('openclaw_root_locators') or []
    def locators(metric):
        if metric == 'broad.documented_format': return [docref]
        if metric == 'broad.classified_content_density' and density_locators: return density_locators
        if metric == 'broad.stable_root_location' and root_locators: return root_locators
        return [native]
    result = {'schema_version': FORMAT_EVIDENCE_SCHEMA_VERSION, 'run_id': context['run_id'], 'configuration_id': 'openclaw', 'repetition': context['repetition'],
      'build': context['build'], 'collected_on': context['collected_on'], 'result_id': context['result_id'], 'observer': observer, 'native_manifest': native,
      'profile': {'schema_version': 'session-bench-format-profile-v1', 'run_id': context['run_id'], 'configuration_id': 'openclaw', 'repetition': context['repetition'], 'broad_evidence': broad},
      'metric_evidence': [{'metric_id': metric, 'observer_ids': [observer['id']], 'native_locators': locators(metric)} for metric in FORMAT_METRICS]}
    validate_format_evidence(result)
    return result
