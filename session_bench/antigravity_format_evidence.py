"""Fail-closed broad evidence for a complete copied Antigravity session directory.

Every value is read from the copied native bytes. A row that needs the whole
session directory stays unresolved until the capture proves that directory.
A property that the native bytes do not hold (a harness name, a format
version) is reported as absent, never filled in from the capture context.
"""
import hashlib
import json
from pathlib import Path

from .antigravity_density import _CHUNK, _STEP_OUTPUT, _jsonl, build_antigravity_density, read_antigravity_family
from .antigravity_live import FAMILY_FORMAT
from .format_response_population import _bound_observer, build_observer_rationale_evidence, observed_response_text_matches
from .live_metric_comparator import (
    _field, _match_action, _match_many, _match_response, _match_turn, _native_id, _native_responses, _observer_events,
    _same_path, _turn_id, _validate_observer,
)
from .native_density import _strict_object
from .v1_public_score import FORMAT_EVIDENCE_SCHEMA_VERSION, FORMAT_METRICS, validate_format_evidence

_EVENT_KINDS = ('user_turn', 'assistant_response', 'action', 'result', 'file_change')


def antigravity_thread_turns(decoded):
    """Turn rows in native step order. The format holds no parent field, so no parent is claimed."""
    roles = {'USER_INPUT': 'user', 'PLANNER_RESPONSE': 'assistant', 'GENERIC': 'tool'}
    return [{'id': f"step:{row['locator']['step_index']}", 'role': roles[row['raw'].get('type')],
             'ordinal': row['locator']['line'], 'parent_id': None}
            for row in decoded['records'] if row['raw'].get('type') in roles]


def antigravity_forward_occurrences(native, *, artifacts, primary):
    """Every semantic event a forward reader of the directory meets, one row per physical occurrence.

    The two transcripts and their chunk copies hold the same steps with the
    same ``step_index``. A step output file repeats the text of its result. No
    field marks a copy as superseded, so every occurrence stays active.
    """
    native, base = Path(native), primary.split('/', 1)[0] + '/.system_generated/'
    occurrences = []
    for name in sorted(row['path'] for row in artifacts):
        relative = name[len(base):] if name.startswith(base) else ''
        if relative in ('logs/transcript.jsonl', 'logs/transcript_full.jsonl') or _CHUNK.fullmatch(relative):
            for number, raw in _jsonl((native / name).read_bytes()):
                row = _strict_object(raw)
                step, kind = row.get('step_index'), row.get('type')
                if kind == 'USER_INPUT' or (kind == 'PLANNER_RESPONSE' and any(isinstance(row.get(key), str) and row[key] for key in ('content', 'thinking'))):
                    occurrences.append((f'message:step:{step}', f'{name}:line-{number}:message'))
                if kind == 'PLANNER_RESPONSE':
                    for index, _ in enumerate(row.get('tool_calls') or []):
                        occurrences.append((f'call:step:{step}:{index}', f'{name}:line-{number}:call-{index}'))
                elif kind == 'GENERIC':
                    occurrences.append((f'result:step:{step}', f'{name}:line-{number}'))
        elif _STEP_OUTPUT.fullmatch(relative):
            occurrences.append((f'result:step:{int(_STEP_OUTPUT.fullmatch(relative).group(1))}', name))
    return occurrences


def antigravity_event_timestamps(decoded, *, observer, run_id, observer_document, complete_record_family,
                                 actions=None, unit='rfc3339', time_zone='native RFC3339 offset'):
    """Timestamp of each observed event, read from the native line of that event.

    A turn, a response and an action use the comparator's match rules. A
    result is the native step the observer names (``native_result_id``), the
    same identity ``work.results`` uses. A changed file is the native change
    with the observed path and both observed hashes. Two native calls that
    match one observed action give two rows for it.
    """
    document = _bound_observer(observer_document, observer, run_id)
    _, events, _ = _validate_observer(document)
    by_kind = {kind: _observer_events(events, kind) for kind in _EVENT_KINDS}
    required = [row['id'] for row in events if row['kind'] in _EVENT_KINDS and row['population_role'] == 'primary_scored']
    _, _, matched_turns, _ = _match_many(by_kind['user_turn'], decoded['turns'], _match_turn)
    turn_map = {_native_id(candidate): expected for expected, candidate in matched_turns.items()}

    def result_match(row, candidate):
        fields = row['fields']
        if not isinstance(fields.get('native_result_id'), str) or candidate.get('id') != fields['native_result_id']:
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
        'action': lambda row, candidate: _match_action(row, candidate, turn_map).value is True,
        'result': result_match, 'file_change': change_match,
    }
    candidates = {'user_turn': decoded['turns'], 'assistant_response': _native_responses(decoded),
                  'action': decoded['actions'] if actions is None else actions, 'result': decoded['results'], 'file_change': decoded['file_changes']}
    stamps = {row['locator']['line']: row.get('timestamp') for row in decoded['records']}
    records = []
    for kind in _EVENT_KINDS:
        for candidate in candidates[kind]:
            matched = [row['id'] for row in by_kind[kind] if matchers[kind](row, candidate)]
            if not matched:
                continue  # unrelated exploration is outside the population
            event_id = matched[0] if len(matched) == 1 else f'ambiguous-native-event:{kind}:{len(records)}'
            records.append({'id': event_id, 'timestamp': stamps.get(candidate['locator']['line']),
                            'unit': unit, 'time_zone': time_zone})
    complete = bool(required) and complete_record_family and decoded['status'] == 'ok' and not decoded['diagnostics']
    return {'evidence_complete': complete, 'event_ids': required, 'records': records}


def build_antigravity_format_evidence(decoded, *, context, common, primary):
    complete = common.get('complete_record_family', context.get('complete_record_family')) is True and decoded['status'] == 'ok'
    observer = common['observer']; native = common['native_manifest']
    documentation = Path(__file__).resolve().parents[1] / 'docs/survival-v1/adapters/antigravity.md'
    docref = {'id': 'documentation:antigravity-step-jsonl-v2', 'sha256': hashlib.sha256(documentation.read_bytes()).hexdigest()}
    session = decoded['session_id'] if isinstance(decoded.get('session_id'), str) else ''

    # Companions of the copied directory: contract exceptions, identity, duplicates, density.
    exceptions, density, density_locators, occurrences, recipients = list(decoded['diagnostics']), None, [], [], set()
    package = common.get('native_package')
    if complete and package is not None:
        family = Path(package) / 'capture'
        inventory = [{**row, 'path': row['path'][len('capture/'):]} for row in json.loads((Path(package) / 'decode.json').read_bytes())['artifacts']]
        try:
            _, _, family_exceptions = read_antigravity_family(family, artifacts=inventory, primary=primary)
            exceptions += family_exceptions
            occurrences = antigravity_forward_occurrences(family, artifacts=inventory, primary=primary)
            for row in inventory:
                if '/messages/' in row['path'] and not row['path'].endswith('/read.json'):
                    recipients.add(_strict_object((family / row['path']).read_bytes()).get('recipient'))
        except (ValueError, OSError, KeyError, TypeError) as error:
            exceptions.append({'code': 'family_unreadable', 'detail': str(error)})
        result = build_antigravity_density(family, artifacts=inventory, primary=primary, complete_record_family=True)
        if result.evidence['evidence_complete']:
            density, density_locators = result.evidence, list(result.native_locators)
    # The session id is the directory name inside the bundle. A system message file repeats it as ``recipient``.
    identity_bound = bool(session) and recipients == {session}
    decoded_clean = decoded['status'] == 'ok' and not exceptions
    root_rows = context.get('root_repetitions')
    turns = antigravity_thread_turns(decoded)

    broad = {
      'broad.readable_rationale': build_observer_rationale_evidence(decoded, observer=observer, run_id=context['run_id'], observer_document=common['observer_document'], complete_record_family=complete),
      'broad.thread_structure': {'evidence_complete': complete, 'session_id': session, 'turns': turns, 'explicit_parentage': False},
      'broad.standard_tools_readable': {'evidence_complete': True, 'container': 'jsonl', 'parser': 'Python json for the transcript files and the message files; plain text reader for the step output files', 'vendor_binary_required': False, 'account_required': False, 'backend_required': False, 'network_required': False},
      'broad.documented_format': {'evidence_complete': True, 'document_id': docref['id'], 'mapping': {
          'containers': 'one session directory: .system_generated/logs/transcript.jsonl and transcript_full.jsonl, a chunk copy of each, steps/<step>/output.txt and messages/*.json',
          'record_types': 'USER_INPUT, PLANNER_RESPONSE (text, thinking, tool calls, token counts), GENERIC (tool result) and SYSTEM_MESSAGE steps',
          'identities': 'step_index per step; the conversation id is the directory name and the recipient of a system message file',
          'joins': 'steps belong to the latest USER_INPUT step; steps/<step>/output.txt joins a result by step_index; no key joins a result to its tool call',
          'version_semantics': 'the files hold no format or schema version; the contract is bound to the observed CLI build'}},
      # The bundle names its session. It does not name the harness or the surface.
      'broad.self_contained_identity': {'evidence_complete': complete, 'session_id': session if identity_bound else '',
          'harness': '', 'surface': '', 'record_family': decoded['format'],
          'external_lookup_required': False, 'absolute_path_required': False},
      'broad.declared_format_version': {'evidence_complete': complete, 'format_version': '', 'machine_readable': False, 'bundle_binding': native['id']},
      'broad.event_timestamps': antigravity_event_timestamps(decoded, observer=observer, run_id=context['run_id'], observer_document=common['observer_document'], complete_record_family=complete),
      'broad.honest_version_signal': {'evidence_complete': complete, 'declared_version': '', 'decoder_contract_version': FAMILY_FORMAT,
          'incompatible_schema_distinguished': False, 'matches_decoder_contract': decoded_clean},
      'broad.observed_schema_stability': {'evidence_complete': complete, 'advertised_contract': decoded['format'],
          'observations': [{'build': context['build'], 'observed_on': context['collected_on'], 'decoder_contract': decoded['format'], 'decoded': decoded_clean}],
          'exceptions': exceptions},
      'broad.stable_root_location': {'evidence_complete': complete and bool(root_rows), 'repetitions': root_rows if complete and root_rows else []},
      'broad.naive_reader_duplicate_safety': {'evidence_complete': complete and bool(occurrences),
          'event_ids': list(dict.fromkeys(identity for identity, _ in occurrences)),
          'forward_records': [{'event_id': identity, 'occurrence_id': occurrence, 'state': 'active'} for identity, occurrence in occurrences],
          'deduplication': {'documented': True, 'rule': 'step_index names a step in every copy; no field marks one copy as superseded, so every copy stays an active occurrence'}},
      'broad.classified_content_density': density or {'evidence_complete': False, 'classification_rule': 'logical-record-role-v1', 'records': []},
    }
    root_locators = common.get('antigravity_root_locators') or []
    def locators(metric):
        if metric == 'broad.documented_format': return [docref]
        if metric == 'broad.classified_content_density' and density_locators: return density_locators
        if metric == 'broad.stable_root_location' and root_locators: return root_locators
        return [native]
    result = {'schema_version': FORMAT_EVIDENCE_SCHEMA_VERSION, 'run_id': context['run_id'], 'configuration_id': 'antigravity', 'repetition': context['repetition'],
      'build': context['build'], 'collected_on': context['collected_on'], 'result_id': context['result_id'], 'observer': observer, 'native_manifest': native,
      'profile': {'schema_version': 'session-bench-format-profile-v1', 'run_id': context['run_id'], 'configuration_id': 'antigravity', 'repetition': context['repetition'], 'broad_evidence': broad},
      'metric_evidence': [{'metric_id': metric, 'observer_ids': [observer['id']], 'native_locators': locators(metric)} for metric in FORMAT_METRICS]}
    validate_format_evidence(result)
    return result


# --- Whole-state family: the conversation database is the primary read ---
def state_forward_occurrences(store):
    """Every statement of an event inside the primary read, the conversation database.

    A model step row states its text and its tool calls. A tool step row
    states the call again (name and arguments in its metadata) and the result.
    A ``gen_metadata`` row with the messages sent to the model states every
    earlier message, call and result once more. No field marks a statement as
    superseded, so all stay active. The transcript files are outside the read.
    """
    from .antigravity_conversation_db import STEP_MODEL, STEP_SYSTEM, STEP_TOOL, STEP_USER, field, snapshot_statements, text, tool_calls
    occurrences = []
    for row in store['tables'].get('steps', []):
        step, kind, payload, metadata = row.get('idx'), row.get('step_type'), row.get('step_payload'), row.get('metadata')
        if not isinstance(payload, bytes) or not isinstance(metadata, bytes):
            continue
        if kind == STEP_USER or (kind == STEP_MODEL and (field(payload, 20, 1) or field(payload, 20, 3))):
            occurrences.append((f'message:step:{step}', f'steps:{step}:message'))
        if kind == STEP_MODEL:
            for index, call in enumerate(tool_calls(payload)):
                occurrences.append((f'call:{call[0]}', f'steps:{step}:call-{index}'))
        elif kind == STEP_TOOL:
            occurrences.append((f'call:{text(metadata, 4, 1)}', f'steps:{step}:call-copy'))
            occurrences.append((f'result:step:{step}', f'steps:{step}:result'))
    return occurrences + snapshot_statements(store)


def build_state_format_evidence(decoded, projected, *, context, common, session_id):
    from .antigravity_conversation_db import USER_VERSION, ConversationDatabaseError, read_conversation_db
    from .antigravity_density import build_state_density, read_state_family, state_identity
    from .antigravity_live import STATE_FORMAT, conversation_db_path
    complete = common.get('complete_record_family', context.get('complete_record_family')) is True and decoded['status'] == 'ok'
    observer = common['observer']; native = common['native_manifest']
    documentation = Path(__file__).resolve().parents[1] / 'docs/survival-v1/adapters/antigravity.md'
    docref = {'id': 'documentation:antigravity-conversation-db-v1', 'sha256': hashlib.sha256(documentation.read_bytes()).hexdigest()}
    exceptions, density, density_locators, occurrences, identity = list(decoded['diagnostics']), None, [], [], {}
    package = common.get('native_package')
    if complete and package is not None:
        family = Path(package) / 'capture'
        inventory = [{**row, 'path': row['path'][len('capture/'):]} for row in json.loads((Path(package) / 'decode.json').read_bytes())['artifacts']]
        try:
            store = read_conversation_db((family / conversation_db_path(session_id)).read_bytes())
            _, _, family_exceptions = read_state_family(family, artifacts=inventory, session_id=session_id, store=store)
            exceptions += family_exceptions
            occurrences = state_forward_occurrences(store)
            identity = state_identity(store, session_id)
            result = build_state_density(family, artifacts=inventory, session_id=session_id, store=store)
            if result.evidence['evidence_complete']:
                density, density_locators = result.evidence, list(result.native_locators)
        except (ValueError, OSError, KeyError, TypeError) as error:
            exceptions.append({'code': 'family_unreadable', 'detail': str(error)})
    version = decoded.get('user_version')
    declared = str(version) if type(version) is int else ''
    decoded_clean = decoded['status'] == 'ok' and not exceptions
    root_rows = context.get('root_repetitions')
    roles = {14: 'user', 15: 'assistant', 132: 'tool'}
    turns = [{'id': f"step:{row['locator']['step_index']}", 'role': roles[row['raw'].get('step_type')], 'ordinal': row['locator']['line'], 'parent_id': None}
             for row in decoded['records'] if row['raw'].get('step_type') in roles]
    broad = {
      'broad.readable_rationale': build_observer_rationale_evidence(decoded, observer=observer, run_id=context['run_id'], observer_document=common['observer_document'], complete_record_family=complete),
      'broad.thread_structure': {'evidence_complete': complete, 'session_id': identity.get('session_id', ''), 'turns': turns, 'explicit_parentage': False},
      # The container is SQLite, but every record body is a protobuf message without a published schema.
      # A reader needs a protobuf wire decoder besides the SQLite parser, so the assertion fails.
      'broad.standard_tools_readable': {'evidence_complete': True, 'container': 'sqlite with protobuf record bodies', 'parser': 'Python sqlite3 plus a protobuf wire-format reader', 'vendor_binary_required': False, 'account_required': False, 'backend_required': False, 'network_required': False},
      'broad.documented_format': {'evidence_complete': True, 'document_id': docref['id'], 'mapping': {
          'containers': 'conversations/<conversation-id>.db (primary) with mirrors under brain/<conversation-id>/, annotations/<conversation-id>.pbtxt and presence/<conversation-id>.lock',
          'record_types': 'steps rows by step_type (14 user input, 15 model step, 132 tool step, 101 system message); gen_metadata rows per model call; protobuf fields named by number in the adapter document',
          'identities': 'steps.idx; trajectory_meta.cascade_id is the conversation id; tool call id; response id',
          'joins': 'tool step metadata 4.1 equals the tool call id 20.7.1 of a model step; gen_metadata 1.4.7 equals the response id 20.6; a step belongs to the latest user step',
          'version_semantics': 'PRAGMA user_version is the schema version of the database; steps.step_format is the format of a step payload'}},
      'broad.self_contained_identity': {'evidence_complete': complete, 'session_id': identity.get('session_id', ''),
          'harness': identity.get('harness', ''), 'surface': identity.get('surface', ''), 'record_family': decoded['format'],
          'external_lookup_required': False, 'absolute_path_required': False},
      'broad.declared_format_version': {'evidence_complete': complete, 'format_version': declared, 'machine_readable': bool(declared), 'bundle_binding': native['id']},
      'broad.event_timestamps': antigravity_event_timestamps(decoded, observer=observer, run_id=context['run_id'], observer_document=common['observer_document'],
          complete_record_family=complete, actions=projected['actions'], unit='unix_s', time_zone='UTC'),
      # The decoder contract names the user_version and refuses another one.
      'broad.honest_version_signal': {'evidence_complete': complete, 'declared_version': declared,
          'decoder_contract_version': f'{STATE_FORMAT};user_version={USER_VERSION}',
          'incompatible_schema_distinguished': bool(declared), 'matches_decoder_contract': decoded['status'] == 'ok' and version == USER_VERSION},
      'broad.observed_schema_stability': {'evidence_complete': complete, 'advertised_contract': decoded['format'],
          'observations': [{'build': context['build'], 'observed_on': context['collected_on'], 'decoder_contract': decoded['format'], 'decoded': decoded_clean}],
          'exceptions': exceptions},
      'broad.stable_root_location': {'evidence_complete': complete and bool(root_rows), 'repetitions': root_rows if complete and root_rows else []},
      'broad.naive_reader_duplicate_safety': {'evidence_complete': complete and bool(occurrences),
          'event_ids': list(dict.fromkeys(identity for identity, _ in occurrences)),
          'forward_records': [{'event_id': identity, 'occurrence_id': occurrence, 'state': 'active'} for identity, occurrence in occurrences],
          'deduplication': {'documented': True, 'rule': 'the read is the conversation database; steps.idx, the tool call id and the response id name an event, but no field marks a repeated statement as superseded'}},
      'broad.classified_content_density': density or {'evidence_complete': False, 'classification_rule': 'logical-record-role-v1', 'records': []},
    }
    root_locators = common.get('antigravity_root_locators') or []
    def locators(metric):
        if metric == 'broad.documented_format': return [docref]
        if metric == 'broad.classified_content_density' and density_locators: return density_locators
        if metric == 'broad.stable_root_location' and root_locators: return root_locators
        return [native]
    result = {'schema_version': FORMAT_EVIDENCE_SCHEMA_VERSION, 'run_id': context['run_id'], 'configuration_id': 'antigravity', 'repetition': context['repetition'],
      'build': context['build'], 'collected_on': context['collected_on'], 'result_id': context['result_id'], 'observer': observer, 'native_manifest': native,
      'profile': {'schema_version': 'session-bench-format-profile-v1', 'run_id': context['run_id'], 'configuration_id': 'antigravity', 'repetition': context['repetition'], 'broad_evidence': broad},
      'metric_evidence': [{'metric_id': metric, 'observer_ids': [observer['id']], 'native_locators': locators(metric)} for metric in FORMAT_METRICS]}
    validate_format_evidence(result)
    return result
