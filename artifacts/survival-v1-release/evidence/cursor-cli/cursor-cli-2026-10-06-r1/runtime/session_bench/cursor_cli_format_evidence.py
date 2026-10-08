"""Fail-closed broad evidence for a complete copied Cursor CLI session family.

Every value is read from the copied native bytes. A property that the native
bytes do not hold is reported as absent, never filled in from the capture
context.
"""
import hashlib
import json
from pathlib import Path

from .cursor_cli_density import build_cursor_density, read_cursor_family
from .cursor_cli_store import SIDECAR_SCHEMA_VERSION, STORE_FORMAT, USER_VERSION
from .format_response_population import _bound_observer, build_observer_rationale_evidence, observed_response_text_matches
from .live_metric_comparator import (
    _field, _match_action, _match_many, _match_response, _match_turn, _native_id, _native_responses, _observer_events,
    _same_path, _validate_observer,
)
from .v1_public_score import FORMAT_EVIDENCE_SCHEMA_VERSION, FORMAT_METRICS, validate_format_evidence

_EVENT_KINDS = ('user_turn', 'assistant_response', 'action', 'result', 'file_change')


def cursor_thread_turns(decoded):
    """Turn rows with parents from the turn tree: a turn blob names its user message and its steps by blob id.

    A tool step is one row (the call id), also when a compound call gives
    several actions. Returns (turns, explicit).
    """
    rows = [(turn['sequence'], turn['id'], 'user', None) for turn in decoded['turns']]
    rows += [(row['sequence'], row['id'], 'assistant', row['turn_id']) for row in decoded['responses']]
    calls = {}
    for action in decoded['actions']:
        calls.setdefault(action.get('parent_call_id', action['call_id']), (action['sequence'], action['turn_id']))
    rows += [(sequence, call, 'tool', turn) for call, (sequence, turn) in calls.items()]
    turns = [{'id': identity, 'role': role, 'ordinal': sequence, 'parent_id': parent} for sequence, identity, role, parent in sorted(rows, key=lambda row: row[0])]
    explicit = bool(turns) and all(turn['parent_id'] is not None for turn in turns if turn['role'] != 'user')
    return turns, explicit


def cursor_event_timestamps(decoded, projected, *, observer, run_id, observer_document, complete_record_family):
    """Timestamp of each observed event, read from the native record of that event.

    A turn, a response and an action use the comparator's match rules. A
    result is the native result of the native action that pairs with the
    observed action. A changed file is the native change with the observed
    path and both observed hashes. The times are Unix milliseconds: the
    creation time of a user message, the start time of a text or tool step,
    the end time of a tool step for its result and for the file change.
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
        return all(fields.get(key) is None or candidate.get(key) is None or fields[key] == candidate[key]
                   for key in ('output', 'status', 'exit_code', 'helper_nonce'))

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
                continue  # an unscored native action is outside the population
            event_id = matched[0] if len(matched) == 1 else f'ambiguous-native-event:{kind}:{len(records)}'
            records.append({'id': event_id, 'timestamp': candidate.get('timestamp'), 'unit': 'unix_ms', 'time_zone': 'UTC'})
    complete = bool(required) and complete_record_family and decoded['status'] == 'ok' and not decoded['diagnostics']
    return {'evidence_complete': complete, 'event_ids': required, 'records': records}


def build_cursor_format_evidence(decoded, projected, *, context, common):
    complete = common.get('complete_record_family', context.get('complete_record_family')) is True and decoded['status'] == 'ok'
    observer = common['observer']; native = common['native_manifest']
    documentation = Path(__file__).resolve().parents[1] / 'docs/survival-v1/adapters/cursor.md'
    docref = {'id': 'documentation:cursor-cli-chat-store-v1', 'sha256': hashlib.sha256(documentation.read_bytes()).hexdigest()}
    exceptions, density, density_locators, occurrences = list(decoded['diagnostics']), None, [], []
    package = common.get('native_package')
    if complete and package is not None:
        inventory = json.loads((Path(package) / 'decode.json').read_bytes())['artifacts']
        try:
            _, _, family_exceptions, occurrences = read_cursor_family(package, artifacts=inventory)
            exceptions += [item for item in family_exceptions if item not in exceptions]
            result = build_cursor_density(package, artifacts=inventory, complete_record_family=True)
            if result.evidence['evidence_complete']:
                density, density_locators = result.evidence, list(result.native_locators)
        except (ValueError, OSError, KeyError, TypeError) as error:
            exceptions.append({'code': 'family_unreadable', 'detail': str(error)})
    # Both declared versions are read from the native bytes. The contract names both and refuses another value.
    store_version, sidecar_version = decoded.get('user_version'), decoded.get('sidecar_schema_version')
    declared = (f'meta.json schemaVersion={sidecar_version};store.db user_version={store_version}'
                if type(store_version) is int and type(sidecar_version) is int else '')
    contract = f'{STORE_FORMAT};meta.json schemaVersion={SIDECAR_SCHEMA_VERSION};store.db user_version={USER_VERSION}'
    versions_match = decoded['status'] == 'ok' and store_version == USER_VERSION and sidecar_version == SIDECAR_SCHEMA_VERSION
    turns, explicit = cursor_thread_turns(decoded)
    session = decoded['session_id'] if isinstance(decoded.get('session_id'), str) and decoded['status'] == 'ok' else ''
    decoded_clean = decoded['status'] == 'ok' and not exceptions
    root_rows = context.get('root_repetitions')
    broad = {
      'broad.readable_rationale': build_observer_rationale_evidence(decoded, observer=observer, run_id=context['run_id'], observer_document=common['observer_document'], complete_record_family=complete),
      'broad.thread_structure': {'evidence_complete': complete, 'session_id': session, 'turns': turns, 'explicit_parentage': explicit},
      # The container is SQLite. The message blobs are JSON, but the root, the turns and the steps (order, times, call
      # records, the exact prompt) are protobuf messages without a published schema. A reader needs a protobuf
      # wire decoder besides the SQLite and JSON parsers, so the assertion fails.
      'broad.standard_tools_readable': {'evidence_complete': True, 'container': 'sqlite with JSON and protobuf blob bodies', 'parser': 'Python sqlite3 and json plus a protobuf wire-format reader', 'vendor_binary_required': False, 'account_required': False, 'backend_required': False, 'network_required': False},
      'broad.documented_format': {'evidence_complete': True, 'document_id': docref['id'], 'mapping': {
          'containers': 'chats/<workspace-hash>/<session-id>/store.db (tables meta and blobs) with the sidecar meta.json; the agent transcript JSONL under projects/<workspace-key>/agent-transcripts/<session-id>/ is a second copy; rows of the session also sit in the shared store ~/.cursor/ai-tracking/ai-code-tracking.db, which is outside the read',
          'record_types': 'blob classes message (JSON), root, turn, user_message, step (text, tool call with result, thinking) and file_content; protobuf fields named by number in the adapter document',
          'identities': 'meta.agentId is the session id; a blob id is the SHA-256 of its bytes; user message id; tool call id',
          'joins': 'meta.latestRootBlobId to the root; root field 8 to turns; turn to its user message and steps by blob id; tool call id of a step equals toolCallId of the JSON messages; a text step and its JSON text part by position in the turn and equal text',
          'version_semantics': 'meta.json schemaVersion is the schema version of the session directory; PRAGMA user_version is the schema version of the store; neither is the application build'}},
      # Session id from the meta row; harness from the provider namespace of the JSON messages; surface from root field 22.
      'broad.self_contained_identity': {'evidence_complete': complete, 'session_id': session,
          'harness': decoded.get('harness') or '', 'surface': decoded.get('surface') or '', 'record_family': decoded['format'],
          'external_lookup_required': False, 'absolute_path_required': False},
      'broad.declared_format_version': {'evidence_complete': complete, 'format_version': declared, 'machine_readable': bool(declared), 'bundle_binding': native['id']},
      'broad.event_timestamps': cursor_event_timestamps(decoded, projected, observer=observer, run_id=context['run_id'],
          observer_document=common['observer_document'], complete_record_family=complete),
      'broad.honest_version_signal': {'evidence_complete': complete, 'declared_version': declared, 'decoder_contract_version': contract,
          'incompatible_schema_distinguished': bool(declared), 'matches_decoder_contract': versions_match},
      'broad.observed_schema_stability': {'evidence_complete': complete, 'advertised_contract': decoded['format'],
          'observations': [{'build': context['build'], 'observed_on': context['collected_on'], 'decoder_contract': decoded['format'], 'decoded': decoded_clean}],
          'exceptions': exceptions},
      'broad.stable_root_location': {'evidence_complete': complete and bool(root_rows), 'repetitions': root_rows if complete and root_rows else []},
      'broad.naive_reader_duplicate_safety': {'evidence_complete': complete and bool(occurrences),
          'event_ids': list(dict.fromkeys(identity for identity, _ in occurrences)),
          'forward_records': [{'event_id': identity, 'occurrence_id': occurrence, 'state': 'active'} for identity, occurrence in occurrences],
          'deduplication': {'documented': True, 'rule': 'the read is the chat store, its sidecar and the agent transcript; a prompt is named by its message id, a text or thinking step by its blob id, a call and its result by the call id; the JSON message list, the turn tree and the transcript state the same events and no field marks one statement as superseded'}},
      'broad.classified_content_density': density or {'evidence_complete': False, 'classification_rule': 'logical-record-role-v1', 'records': []},
    }
    root_locators = common.get('cursor_root_locators') or []
    def locators(metric):
        if metric == 'broad.documented_format': return [docref]
        if metric == 'broad.classified_content_density' and density_locators: return density_locators
        if metric == 'broad.stable_root_location' and root_locators: return root_locators
        return [native]
    result = {'schema_version': FORMAT_EVIDENCE_SCHEMA_VERSION, 'run_id': context['run_id'], 'configuration_id': 'cursor-cli', 'repetition': context['repetition'],
      'build': context['build'], 'collected_on': context['collected_on'], 'result_id': context['result_id'], 'observer': observer, 'native_manifest': native,
      'profile': {'schema_version': 'session-bench-format-profile-v1', 'run_id': context['run_id'], 'configuration_id': 'cursor-cli', 'repetition': context['repetition'], 'broad_evidence': broad},
      'metric_evidence': [{'metric_id': metric, 'observer_ids': [observer['id']], 'native_locators': locators(metric)} for metric in FORMAT_METRICS]}
    validate_format_evidence(result)
    return result
