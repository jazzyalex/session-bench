"""Fail-closed broad evidence for the bound rows of one Hermes session.

Every value is read from the row export and the schema export of the packet.
A property that these bytes do not hold is reported as absent, never filled
in from the capture context.
"""
import hashlib
import json
from pathlib import Path

from .format_response_population import _bound_observer, build_observer_rationale_evidence, observed_response_text_matches
from .hermes_store_rows import APPLICATION_ID, FORMAT, SUPPORTED_SCHEMA_VERSIONS, USER_VERSION, build_hermes_density, read_hermes_family
from .live_metric_comparator import (
    _field, _match_action, _match_many, _match_response, _match_turn, _native_id, _native_responses, _observer_events,
    _same_path, _validate_observer,
)
from .v1_public_score import FORMAT_EVIDENCE_SCHEMA_VERSION, FORMAT_METRICS, validate_format_evidence

_EVENT_KINDS = ('user_turn', 'assistant_response', 'action', 'result', 'file_change')


def hermes_thread_turns(decoded):
    """One row per message row, in ``messages.id`` order. Returns (turns, explicit).

    The rows hold no parent key: a response and a tool row belong to the
    latest user row before them. So ``explicit`` is false and the frozen
    linear recovery rule applies. A tool call sits on its assistant row; the
    tool row of its result is its own row.
    """
    rows = {turn['sequence']: (turn['id'], 'user') for turn in decoded['turns']}
    for row in decoded['responses']:
        rows[row['sequence']] = (row['id'], 'assistant')
    for row in decoded['actions']:
        rows.setdefault(row['sequence'], (f"messages:{row['sequence']}", 'assistant'))
    for row in decoded['results']:
        rows.setdefault(row['sequence'], (f"messages:{row['sequence']}", 'tool'))
    turns = [{'id': identity, 'role': role, 'ordinal': sequence, 'parent_id': None} for sequence, (identity, role) in sorted(rows.items())]
    return turns, False


def hermes_event_timestamps(decoded, projected, *, observer, run_id, observer_document, complete_record_family, strict_results=False):
    """Timestamp of each observed event, read from the native row of that event.

    A turn, a response and an action use the comparator's match rules. A
    result is the native result of the native action that pairs with the
    observed action. The time is ``messages.timestamp`` of the row: Unix
    seconds as a real number (UTC). A tool call has the time of its assistant
    row and a result the time of its tool row. The rows hold no file-change
    record, so a changed file has no native time.
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
        if strict_results:
            # A result gets time credit only when the work metric accepts it: every observed value is in the native result.
            return all(fields.get(key) is None or fields[key] == candidate.get(key) for key in ('output', 'status', 'exit_code', 'helper_nonce'))
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
                continue
            event_id = matched[0] if len(matched) == 1 else f'ambiguous-native-event:{kind}:{len(records)}'
            records.append({'id': event_id, 'timestamp': candidate.get('timestamp'), 'unit': 'unix_s', 'time_zone': 'UTC'})
    complete = bool(required) and complete_record_family and decoded['status'] == 'ok' and not decoded['diagnostics']
    return {'evidence_complete': complete, 'event_ids': required, 'records': records}


def build_hermes_format_evidence(decoded, projected, *, context, common):
    complete = common.get('complete_record_family', context.get('complete_record_family')) is True and decoded['status'] == 'ok'
    observer = common['observer']; native = common['native_manifest']
    documentation = Path(__file__).resolve().parents[1] / 'docs/survival-v1/adapters/hermes.md'
    docref = {'id': 'documentation:' + FORMAT, 'sha256': hashlib.sha256(documentation.read_bytes()).hexdigest()}
    exceptions, density, density_locators, occurrences = list(decoded['diagnostics']), None, [], []
    package = common.get('native_package')
    if complete and package is not None:
        inventory = json.loads((Path(package) / 'decode.json').read_bytes())['artifacts']
        try:
            _, _, family_exceptions, occurrences = read_hermes_family(package, artifacts=inventory)
            exceptions += [item for item in family_exceptions if item not in exceptions]
            result = build_hermes_density(package, artifacts=inventory, complete_record_family=True)
            if result.evidence['evidence_complete']:
                density, density_locators = result.evidence, list(result.native_locators)
        except (ValueError, OSError, KeyError, TypeError) as error:
            exceptions.append({'code': 'family_unreadable', 'detail': str(error)})
    # The declared schema version is the row of the store table ``schema_version``. It names no session, so the
    # row export does not hold it. Without the bound version extract the value is unknown: both version rows
    # stay unresolved. ``PRAGMA user_version`` is 0 (not set) and ``application_id`` is not a version.
    version = decoded.get('schema_version')
    known = type(version) is int
    declared = f'state.db schema_version={version}' if known else ''
    contract = f"{FORMAT};state.db application_id={APPLICATION_ID};user_version={USER_VERSION};schema_version in {list(SUPPORTED_SCHEMA_VERSIONS)}"
    versions_match = decoded['status'] == 'ok' and known and version in SUPPORTED_SCHEMA_VERSIONS
    turns, explicit = hermes_thread_turns(decoded)
    session = decoded['session_id'] if isinstance(decoded.get('session_id'), str) and decoded['status'] == 'ok' else ''
    decoded_clean = decoded['status'] == 'ok' and not exceptions
    root_rows = context.get('root_repetitions')
    broad = {
      'broad.readable_rationale': build_observer_rationale_evidence(decoded, observer=observer, run_id=context['run_id'], observer_document=common['observer_document'], complete_record_family=complete),
      'broad.thread_structure': {'evidence_complete': complete, 'session_id': session, 'turns': turns, 'explicit_parentage': explicit},
      # The container is SQLite. Every value that a fact needs is an INTEGER, a REAL or TEXT; tool calls, tool
      # results and the request configuration are JSON text. The one BLOB column (display_identity) and the
      # encrypted reasoning text carry no required fact.
      'broad.standard_tools_readable': {'evidence_complete': True, 'container': 'sqlite', 'parser': 'Python sqlite3 and json', 'vendor_binary_required': False, 'account_required': False, 'backend_required': False, 'network_required': False},
      'broad.documented_format': {'evidence_complete': True, 'document_id': docref['id'], 'mapping': {
          'containers': 'the shared store ~/.hermes/state.db (SQLite); the session is its rows in the tables sessions, messages and session_model_usage and the system_prompts row that the session row names; the capture binds these rows as JSON row exports with the schema of the store',
          'record_types': 'a sessions row (identity, model, request configuration, totals); a messages row with role user, assistant or tool; a session_model_usage row per model and task (totals); a system_prompts row (hash, prompt)',
          'identities': 'sessions.id is the session id; messages.id is the row id of a message and its order; messages.message_uid; the call id inside messages.tool_calls and messages.tool_call_id',
          'joins': 'messages.session_id and session_model_usage.session_id reference sessions.id (declared foreign keys); sessions.system_prompt_hash is the SHA-256 of system_prompts.prompt and equals system_prompts.hash; a tool row names its call by tool_call_id; a response belongs to the latest user row before it in messages.id order',
          'version_semantics': 'the table schema_version holds the schema version of the store; PRAGMA user_version is 0 and not used; PRAGMA application_id names the application, not a version; none is the application build'}},
      # Session id from the sessions row; surface from sessions.source; harness from the first sentence of the bound system prompt row.
      'broad.self_contained_identity': {'evidence_complete': complete, 'session_id': session,
          'harness': decoded.get('harness') or '', 'surface': decoded.get('surface') or '', 'record_family': decoded['format'],
          'external_lookup_required': False, 'absolute_path_required': False},
      'broad.declared_format_version': {'evidence_complete': complete and known, 'format_version': declared, 'machine_readable': bool(declared),
          'bundle_binding': (common.get('hermes_version_locator') or native)['id']},
      'broad.event_timestamps': hermes_event_timestamps(decoded, projected, observer=observer, run_id=context['run_id'],
          observer_document=common['observer_document'], complete_record_family=complete,
          strict_results=common.get('strict_result_times') is True),
      'broad.honest_version_signal': {'evidence_complete': complete and known, 'declared_version': declared, 'decoder_contract_version': contract,
          'incompatible_schema_distinguished': bool(declared), 'matches_decoder_contract': versions_match},
      'broad.observed_schema_stability': {'evidence_complete': complete, 'advertised_contract': decoded['format'],
          'observations': [{'build': context['build'], 'observed_on': context['collected_on'], 'decoder_contract': decoded['format'], 'decoded': decoded_clean}],
          'exceptions': exceptions},
      'broad.stable_root_location': {'evidence_complete': complete and bool(root_rows), 'repetitions': root_rows if complete and root_rows else []},
      'broad.naive_reader_duplicate_safety': {'evidence_complete': complete and bool(occurrences),
          'event_ids': list(dict.fromkeys(identity for identity, _ in occurrences)),
          'forward_records': [{'event_id': identity, 'occurrence_id': occurrence, 'state': 'active'} for identity, occurrence in occurrences],
          'deduplication': {'documented': True, 'rule': 'the read is the rows of the session in the tables sessions, messages and session_model_usage and its system_prompts row; a prompt and a response are named by messages.id, a call and its result by the call id; one messages row is one record and no column marks a row of these sessions as superseded'}},
      'broad.classified_content_density': density or {'evidence_complete': False, 'classification_rule': 'logical-record-role-v1', 'records': []},
    }
    root_locators = common.get('hermes_root_locators') or []
    def locators(metric):
        if metric == 'broad.documented_format': return [docref]
        if metric == 'broad.classified_content_density' and density_locators: return density_locators
        if metric == 'broad.stable_root_location' and root_locators: return root_locators
        if metric in ('broad.declared_format_version', 'broad.honest_version_signal') and common.get('hermes_version_locator'):
            return [common['hermes_version_locator']]
        return [native]
    result = {'schema_version': FORMAT_EVIDENCE_SCHEMA_VERSION, 'run_id': context['run_id'], 'configuration_id': 'hermes', 'repetition': context['repetition'],
      'build': context['build'], 'collected_on': context['collected_on'], 'result_id': context['result_id'], 'observer': observer, 'native_manifest': native,
      'profile': {'schema_version': 'session-bench-format-profile-v1', 'run_id': context['run_id'], 'configuration_id': 'hermes', 'repetition': context['repetition'], 'broad_evidence': broad},
      'metric_evidence': [{'metric_id': metric, 'observer_ids': [observer['id']], 'native_locators': locators(metric)} for metric in FORMAT_METRICS]}
    validate_format_evidence(result)
    return result
