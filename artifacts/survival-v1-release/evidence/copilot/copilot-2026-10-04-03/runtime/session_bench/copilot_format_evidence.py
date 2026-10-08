"""Fail-closed broad evidence for the retained Copilot session family.

Every value is read from the copied native bytes. A row that needs the whole
session directory stays unresolved until the capture proves that directory.
"""
import hashlib
import json
from pathlib import Path
from .copilot_density import build_copilot_density, parse_workspace_yaml, read_copilot_family
from .native_density import holds_call_arguments, string_leaves
from .copilot_live import DECODER_CONTRACT, NATIVE_SCHEMA_VERSION
from .copilot_session_store import STORE_SCHEMA_VERSION
from .format_response_population import build_observer_rationale_evidence
from .format_timestamp_population import build_observer_timestamp_evidence
from .v1_public_score import FORMAT_EVIDENCE_SCHEMA_VERSION, FORMAT_METRICS, validate_format_evidence


def _event_rows(decoded):
    return [(row['locator']['line'], row['raw']) for row in decoded['records'] if row['locator']['artifact'] == 'events.jsonl']


def copilot_thread_turns(decoded):
    """Turn rows with parents from explicit native joins.

    An assistant message names its user message (``originatingMessageId`` =
    ``messageId``). A tool completion names its call (``toolCallId``), and the
    call sits in one assistant message. Returns (turns, explicit). When one
    join is missing, no parent is claimed and ``explicit`` is False.
    """
    users, requests, turns = {}, {}, []
    for line, row in _event_rows(decoded):
        data, kind, identity = row.get('data', {}), row.get('type'), row.get('id')
        if not isinstance(identity, str) or not isinstance(data, dict):
            continue
        if kind == 'user.message':
            if isinstance(data.get('messageId'), str): users[data['messageId']] = identity
            turns.append({'id': identity, 'role': 'user', 'ordinal': line, 'parent_id': None})
        elif kind == 'assistant.message':
            for request in data.get('toolRequests') or []:
                if isinstance(request, dict) and isinstance(request.get('toolCallId'), str):
                    requests[request['toolCallId']] = identity
            turns.append({'id': identity, 'role': 'assistant', 'ordinal': line, 'parent_id': users.get(data.get('originatingMessageId'))})
        elif kind == 'tool.execution_complete':
            turns.append({'id': identity, 'role': 'tool', 'ordinal': line, 'parent_id': requests.get(data.get('toolCallId'))})
    explicit = bool(turns) and all(turn['parent_id'] is not None for turn in turns if turn['role'] != 'user')
    if not explicit:
        turns = [{**turn, 'parent_id': None} for turn in turns]
    return turns, explicit


def copilot_forward_occurrences(decoded):
    """Every semantic event a forward reader of ``events.jsonl`` meets, one row per record that states it.

    A tool call is written twice: as a request in ``assistant.message`` and as
    ``tool.execution_start``. Both carry the name and the arguments and share
    ``toolCallId``. No field marks one of them as superseded, so both stay
    active and the call does not pass the naive-reader check. A completion
    record restates its call when it holds all text arguments of the call. A
    call whose only argument is a path (``view``) is restated only by a record
    that also states the tool name: the completion names it in its telemetry
    (``toolTelemetry.properties.command``).
    """
    occurrences, requested = [], {}
    for line, row in _event_rows(decoded):
        data, kind, identity = row.get('data', {}), row.get('type'), row.get('id')
        if not isinstance(data, dict):
            continue
        if kind == 'user.message':
            occurrences.append((f'message:{identity}', f'line-{line}'))
        elif kind == 'assistant.message':
            if any(isinstance(data.get(key), str) and data[key] for key in ('content', 'reasoningText')):
                occurrences.append((f'message:{identity}', f'line-{line}:message'))
            for index, request in enumerate(data.get('toolRequests') or []):
                if isinstance(request, dict):
                    occurrences.append((f'call:{request.get("toolCallId")}', f'line-{line}:request-{index}'))
                    requested[request.get('toolCallId')] = (request.get('name'), request.get('arguments'))
        elif kind == 'tool.execution_start':
            occurrences.append((f'call:{data.get("toolCallId")}', f'line-{line}'))
        elif kind == 'tool.execution_complete':
            occurrences.append((f'result:{data.get("toolCallId")}', f'line-{line}'))
            # A completion record that holds all text arguments of its call restates the call.
            if data.get('toolCallId') in requested:
                name, arguments = requested[data['toolCallId']]
                # The result text is not evidence of the tool name; the telemetry and the other fields are.
                named = isinstance(name, str) and bool(name) and name in string_leaves({key: value for key, value in data.items() if key != 'result'})
                if holds_call_arguments(arguments, data, names_tool=named):
                    occurrences.append((f'call:{data["toolCallId"]}', f'line-{line}:arguments'))
    return occurrences


def copilot_rewind_index_copies(decoded, package):
    """Messages that the rewind snapshot index states again.

    A snapshot names its user event (``eventId``) and holds that prompt as
    ``userMessage``. The same id and the same text prove the copy.
    """
    path = Path(package) / 'rewind-file-snapshots/index.json'
    if not path.is_file():
        return []
    try:
        snapshots = json.loads(path.read_bytes()).get('snapshots')
    except (ValueError, AttributeError):
        return []
    prompts = {row.get('id'): row.get('data', {}).get('content') for _, row in _event_rows(decoded)
               if row.get('type') == 'user.message' and isinstance(row.get('data'), dict)}
    return [(f'message:{item["eventId"]}', f'rewind-file-snapshots/index.json:snapshot-{index}:userMessage')
            for index, item in enumerate(snapshots if isinstance(snapshots, list) else [])
            if isinstance(item, dict) and isinstance(item.get('userMessage'), str) and item['userMessage']
            and prompts.get(item.get('eventId')) == item['userMessage']]


def copilot_backup_copies(decoded, package):
    """Results that a rewind backup file states again: the file holds the full output text of the result."""
    folder = Path(package) / 'rewind-file-snapshots/backups'
    if not folder.is_dir():
        return []
    outputs = [(row.get('data', {}).get('toolCallId'), row['data'].get('result', {}).get('content')) for _, row in _event_rows(decoded)
               if row.get('type') == 'tool.execution_complete' and isinstance(row.get('data'), dict) and isinstance(row['data'].get('result'), dict)]
    copies = []
    for path in sorted(folder.iterdir()):
        if not path.is_file() or path.is_symlink():
            continue
        try:
            text = path.read_bytes().decode('utf-8')
        except UnicodeDecodeError:
            continue
        copies += [(f'result:{call}', f'rewind-file-snapshots/backups/{path.name}' if index == 0 else f'rewind-file-snapshots/backups/{path.name}:result-{index}')
                   for index, (call, output) in enumerate((call, output) for call, output in outputs if isinstance(output, str) and output and output == text)]
    return copies


def build_copilot_format_evidence(decoded, *, context, common):
    complete = common.get('complete_record_family', context.get('complete_record_family')) is True and decoded['status'] == 'ok'
    observer = common['observer']; native = common['native_manifest']
    documentation = Path(__file__).resolve().parents[1] / 'docs/survival-v1/adapters/copilot.md'
    docref = {'id': 'documentation:copilot-session-events-v1', 'sha256': hashlib.sha256(documentation.read_bytes()).hexdigest()}
    rows = _event_rows(decoded)
    start = rows[0][1].get('data', {}) if rows and rows[0][1].get('type') == 'session.start' else {}
    version = decoded.get('native_schema_version')
    declared = str(version) if type(version) is int else ''
    # With the session store in the root, its own schema version is declared
    # too (table ``schema_version``) and the decoder contract names it.
    store = decoded.get('session_store')
    contract = f'{DECODER_CONTRACT};native-schema-version={NATIVE_SCHEMA_VERSION}'
    versions_match = decoded['status'] == 'ok' and version == NATIVE_SCHEMA_VERSION
    if isinstance(store, dict):
        store_version = store.get('schema_version') if store.get('read') else None
        declared = f'events={declared};session-store={store_version}' if declared and type(store_version) is int else ''
        contract += f';session-store-schema-version={STORE_SCHEMA_VERSION}'
        versions_match = versions_match and store_version == STORE_SCHEMA_VERSION
    turns, explicit = copilot_thread_turns(decoded)

    # Companions of the copied directory: identity, contract exceptions, density.
    workspace, exceptions, density, density_locators = {}, list(decoded['diagnostics']), None, []
    package = common.get('native_package')
    if complete and package is not None:
        inventory = json.loads((Path(package) / 'decode.json').read_bytes())['artifacts']
        try:
            _, _, family_exceptions = read_copilot_family(package, artifacts=inventory)
            exceptions += family_exceptions
            if (Path(package) / 'workspace.yaml').is_file():
                workspace = parse_workspace_yaml((Path(package) / 'workspace.yaml').read_bytes()) or {}
        except (ValueError, OSError, KeyError, TypeError) as error:
            exceptions.append({'code': 'family_unreadable', 'detail': str(error)})
        inventory_result = build_copilot_density(package, artifacts=inventory, complete_record_family=True)
        if inventory_result.evidence['evidence_complete']:
            density, density_locators = inventory_result.evidence, list(inventory_result.native_locators)
    if isinstance(store, dict):
        exceptions += store.get('exceptions', [])
    session = decoded['session_id'] if isinstance(decoded['session_id'], str) else ''
    identity_bound = bool(session) and start.get('sessionId') == session and workspace.get('id') == session
    # The read is every container the decoder takes a scored fact from:
    # events.jsonl, the session store (usage) and the rewind snapshot index
    # (changed file). A record there that repeats a message of events.jsonl
    # (same join key, same text) is one more occurrence of that message.
    occurrences = copilot_forward_occurrences(decoded)
    if isinstance(store, dict):
        occurrences += [(f'message:{item["event_id"]}', item['occurrence']) for item in store.get('turn_copies', []) + store.get('cell_copies', [])]
    if complete and package is not None:
        occurrences += copilot_rewind_index_copies(decoded, package) + copilot_backup_copies(decoded, package)
    root_rows = context.get('root_repetitions')
    decoded_clean = decoded['status'] == 'ok' and not exceptions

    broad = {
      'broad.readable_rationale': build_observer_rationale_evidence(decoded, observer=observer, run_id=context['run_id'], observer_document=common['observer_document'], complete_record_family=complete),
      'broad.thread_structure': {'evidence_complete': complete, 'session_id': session, 'turns': turns, 'explicit_parentage': explicit},
      'broad.standard_tools_readable': {'evidence_complete':True,'container':'jsonl','parser':'Python json for events.jsonl and the JSON companions; plain text reader for workspace.yaml and checkpoints; Python sqlite3 for the session store','vendor_binary_required':False,'account_required':False,'backend_required':False,'network_required':False},
      'broad.documented_format': {'evidence_complete':True,'document_id':docref['id'],'mapping':{'containers':'one session directory (events.jsonl plus workspace.yaml, checkpoints and rewind-file-snapshots) and the SQLite session store of COPILOT_HOME','record_types':'session/user/assistant/tool lifecycle and metadata events; rewind snapshot index and backups; session store rows (sessions, turns, assistant_usage_events, search index)','identities':'event id, parentId, messageId, toolCallId and sessionId','joins':'originatingMessageId to messageId; toolCallId request to start to completion; snapshot eventId to user event id; usage rows by session_id, turn_index and id order to assistant.message order','version_semantics':'session.start.data.version is the event log schema version; the session store table schema_version holds the store schema version; copilotVersion is the application build'}},
      # Session id from session.start and workspace.yaml, harness from the
      # native producer field, surface from the native client name.
      'broad.self_contained_identity': {'evidence_complete': complete, 'session_id': session if identity_bound else '',
          'harness': start.get('producer') if isinstance(start.get('producer'), str) else '',
          'surface': workspace.get('client_name', ''), 'record_family': decoded['format'],
          'external_lookup_required': False, 'absolute_path_required': False},
      'broad.declared_format_version': {'evidence_complete': True, 'format_version': declared, 'machine_readable': bool(declared), 'bundle_binding': native['id']},
      'broad.event_timestamps': build_observer_timestamp_evidence(decoded, family='copilot', observer=observer, run_id=context['run_id'], observer_document=common['observer_document'], complete_record_family=complete),
      # The decoder contract names the native version and refuses another one.
      'broad.honest_version_signal': {'evidence_complete': True, 'declared_version': declared,
          'decoder_contract_version': contract,
          'incompatible_schema_distinguished': bool(declared), 'matches_decoder_contract': versions_match},
      'broad.observed_schema_stability': {'evidence_complete': complete, 'advertised_contract': decoded['format'],
          'observations': [{'build': context['build'], 'observed_on': context['collected_on'], 'decoder_contract': decoded['format'], 'decoded': decoded_clean}],
          'exceptions': exceptions},
      'broad.stable_root_location': {'evidence_complete': complete and bool(root_rows), 'repetitions': root_rows if complete and root_rows else []},
      'broad.naive_reader_duplicate_safety': {'evidence_complete': complete and bool(occurrences),
          'event_ids': list(dict.fromkeys(identity for identity, _ in occurrences)) if complete else [],
          'forward_records': [{'event_id': identity, 'occurrence_id': occurrence, 'state': 'active'} for identity, occurrence in occurrences] if complete else [],
          'deduplication': {'documented': True, 'rule': 'message identity is the event id; tool call identity is toolCallId; the request in assistant.message and tool.execution_start are both retained occurrences of one call; the read is events.jsonl, the session store and the rewind snapshot index; a store turns row with the same turn index and text, and a rewind snapshot with the same eventId and userMessage text, are retained occurrences of that message; a completion record with all text arguments of its call restates the call, and a rewind backup file with the full output of a result restates that result'}},
      'broad.classified_content_density': density or {'evidence_complete': False, 'classification_rule': 'logical-record-role-v1', 'records': []},
    }
    root_locators = common.get('copilot_root_locators') or []
    def locators(metric):
        if metric == 'broad.documented_format': return [docref]
        if metric == 'broad.classified_content_density' and density_locators: return density_locators
        if metric == 'broad.stable_root_location' and root_locators: return root_locators
        return [native]
    result = {'schema_version':FORMAT_EVIDENCE_SCHEMA_VERSION,'run_id':context['run_id'],'configuration_id':'copilot','repetition':context['repetition'],
      'build':context['build'],'collected_on':context['collected_on'],'result_id':context['result_id'],'observer':observer,'native_manifest':native,
      'profile':{'schema_version':'session-bench-format-profile-v1','run_id':context['run_id'],'configuration_id':'copilot','repetition':context['repetition'],'broad_evidence':broad},
      'metric_evidence':[{'metric_id':metric,'observer_ids':[observer['id']],'native_locators':locators(metric)} for metric in FORMAT_METRICS]}
    validate_format_evidence(result)
    return result
