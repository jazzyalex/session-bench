"""Broad format evidence for an explicitly captured DSH v4 native artifact."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path

from .dsh_native import read_physical
from .native_density import holds_call_arguments, roles_after_repeats
from .dsh_live import strict_json, decode_dsh_native
from .format_response_population import build_observer_rationale_evidence
from .v1_public_score import FORMAT_EVIDENCE_SCHEMA_VERSION, FORMAT_METRICS, validate_format_evidence


def _strings(value, found):
    """Every string value inside a JSON value."""
    if isinstance(value, str): found.add(value)
    elif isinstance(value, dict):
        for item in value.values(): _strings(item, found)
    elif isinstance(value, list):
        for item in value: _strings(item, found)
    return found


def build_dsh_format_evidence(decoded, *, native_path, observer_document, run_id, repetition,
                              build, collected_on, native_manifest, complete_record_family=False,
                              native_companion=None, root_repetitions=None, allow_stale_loss_cache=False):
    raw, physical = read_physical(Path(native_path))
    if physical != decoded.get('physical') or decoded.get('format') != 'dsh-native-v4':
        raise ValueError('DSH physical source differs from decoded provenance')
    if decoded != decode_dsh_native(Path(native_path)):
        raise ValueError('DSH semantic decode differs from native bytes')
    rows = [strict_json(line) for line in raw.splitlines()]
    if rows[0].get('id') != decoded.get('session_id') or rows[0].get('version') != 4:
        raise ValueError('DSH native header identity mismatch')
    complete = complete_record_family is True and decoded.get('status') == 'ok' and not decoded.get('diagnostics')
    cache = None
    if native_companion is not None:
        from .dsh_cache import read_dsh_cache
        from .dsh_live import DSHSemanticError
        try:
            cache = read_dsh_cache(native_companion, header=rows[0], decoded=decoded)
        except DSHSemanticError:
            if not allow_stale_loss_cache:
                raise
            complete = False
    observer = {'id': 'observer:' + run_id, 'sha256': hashlib.sha256(observer_document).hexdigest()}
    native = {'id': 'native:' + Path(native_path).name, 'sha256': physical['physical_sha256']}
    documentation_path = Path(__file__).resolve().parents[1]/'docs/survival-v1/adapters/deepseek-harness.md'
    documentation = {'id':'documentation:dsh-v4','sha256':hashlib.sha256(documentation_path.read_bytes()).hexdigest()}
    density = []
    semantic_occurrences = []
    call_arguments = {}
    for number,row in enumerate(rows):
        kind = {'assistant/message':'assistant_message','tool/call':'tool_call','tool/result':'tool_result'}.get(row['type'],'metadata')
        if row['type']=='assistant/message':
            content=row['data']['message']['content']
            if not any(b.get('type') in {'text','tool-call'} for b in content):
                kind='unknown'
        if row['type']=='user/message' and row['data'].get('source',{}).get('kind')=='user':kind='user_message'
        # An inbox record holds the whole queued prompt before its user/message record.
        queued=[m for m in row.get('data',{}).get('inserted',[]) if isinstance(m,dict) and m.get('role')=='user'
                and isinstance(m.get('source'),dict) and m['source'].get('kind')=='user' and isinstance(m.get('id'),str)] if row['type']=='agent/inbox/spliced' and isinstance(row.get('data'),dict) and isinstance(row['data'].get('inserted'),list) else []
        if queued:kind='user_message'
        density.append({'record_id':f'line-{number+1}','record_kind':kind,
                        'logical_bytes':len(json.dumps(row,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()),
                        'classification':'unclassified' if kind=='metadata' else 'unknown' if kind=='unknown' else 'useful'})
        data = row.get('data', {})
        if row['type'] == 'user/message' and data.get('source', {}).get('kind') == 'user':
            semantic_occurrences.append((f'message:{data["id"]}', f'line-{number+1}'))
        elif queued:
            semantic_occurrences += [(f'message:{m["id"]}', f'line-{number+1}' if len(queued)==1 else f'line-{number+1}:inserted-{index}') for index,m in enumerate(queued)]
        elif row['type'] == 'assistant/message':
            message = data['message']
            if any(b.get('type') in {'text','reasoning'} for b in message['content']):
                semantic_occurrences.append((f'message:{message["id"]}', f'line-{number+1}:message'))
            for index,block in enumerate(message['content']):
                if block.get('type') == 'tool-call':
                    semantic_occurrences.append((f'call:{data["turn"]}:{data["step"]}:{block["id"]}',f'line-{number+1}:block-{index}'))
                    call_arguments[(data['turn'], data['step'], block['id'])] = block.get('arguments')
        elif row['type'] == 'tool/call':
            semantic_occurrences.append((f'call:{data["turn"]}:{data["step"]}:{data["callId"]}',f'line-{number+1}'))
            call_arguments[(data['turn'], data['step'], data['callId'])] = data.get('arguments')
        elif row['type'] == 'tool/result':
            semantic_occurrences.append((f'message:{data["message"]["id"]}',f'line-{number+1}'))
            # A result record that holds all text arguments of its call restates the call.
            call = (data.get('turn'), data.get('step'), data['message'].get('toolCallId'))
            if call in call_arguments and holds_call_arguments(call_arguments[call], data):
                semantic_occurrences.append((f'call:{call[0]}:{call[1]}:{call[2]}', f'line-{number+1}:arguments'))
    # The read is the session file. The first record that states an event
    # keeps its role; a later record that only repeats stated events (a
    # tool/call after the tool-call block of its message) is a snapshot.
    stated = {}
    for identity, occurrence in semantic_occurrences:
        stated.setdefault(occurrence.split(':')[0], []).append(identity)
    for record, role in zip(density, roles_after_repeats([r['record_kind'] for r in density], [stated.get(r['record_id'], ()) for r in density])):
        if role != record['record_kind']:
            record.update(record_kind=role, classification='unclassified')
    if cache is not None:
        # Reconciliation is scored from the projection cache, so the cache is
        # in the read. A unit that holds the exact text of a message states
        # that message again; the cache record is then a snapshot.
        texts = {}
        for row in rows:
            data = row.get('data', {})
            message = data if row['type'] == 'user/message' and data.get('source', {}).get('kind') == 'user' else data.get('message') if row['type'] in {'assistant/message','tool/result'} else None
            if isinstance(message, dict) and isinstance(message.get('content'), list):
                for text in [''.join(b.get('text','') for b in message['content'] if b.get('type')=='text')] + [b.get('text') for b in message['content'] if b.get('type')=='text']:
                    if isinstance(text, str) and text: texts.setdefault(text, f'message:{message["id"]}')
        record = dict(cache['density_record'])
        units = strict_json(Path(native_companion).read_bytes())['record']['rows']
        copies = [(event, f'{record["record_id"]}:{name}') for name in units for event in sorted({texts[value] for value in _strings(units[name], set()) if value in texts})]
        if copies:
            semantic_occurrences += copies
            record.update(record_kind='snapshot', classification='unclassified')
        density.append(record)
    ordered = sorted(decoded['turns']+[r for r in decoded['responses'] if r.get('phase')=='final_answer'],key=lambda r:r['sequence'])
    turns = [{'id':r['id'],'role':r['role'],'ordinal':index+1,
              'parent_id':r['turn_id'] if r['role']=='assistant' else None} for index,r in enumerate(ordered)]
    timestamp = {'evidence_complete':False,'event_ids':[],'records':[]}
    from .format_timestamp_population import build_observer_timestamp_evidence
    timestamp = build_observer_timestamp_evidence(decoded,family='dsh',observer=observer,run_id=run_id,
        observer_document=observer_document,complete_record_family=complete)
    broad = {
        'broad.readable_rationale':build_observer_rationale_evidence(decoded,observer=observer,run_id=run_id,
            observer_document=observer_document,complete_record_family=complete),
        'broad.thread_structure':{'evidence_complete':complete,'session_id':decoded['session_id'],'turns':turns,'explicit_parentage':True},
        'broad.standard_tools_readable':{'evidence_complete':True,'container':'jsonl','parser':'libzstd plus Python json',
            'vendor_binary_required':False,'account_required':False,'backend_required':False,'network_required':False},
        'broad.documented_format':{'evidence_complete':True,'document_id':documentation['id'],'mapping':{
            'containers':'v4 JSONL or independently framed Zstandard JSONL','record_types':'session header plus typed seq/time/data events',
            'identities':'header id; message id; turn and step; callId','joins':'turn/step/callId lifecycle joins',
            'version_semantics':'native header version 4 with explicit generation filename'}},
        'broad.self_contained_identity':{'evidence_complete':complete,'session_id':decoded['session_id'],'harness':'dsh','surface':'cli',
            'record_family':'dsh-native-v4','external_lookup_required':False,'absolute_path_required':False},
        'broad.declared_format_version':{'evidence_complete':complete,'format_version':'4','machine_readable':True,'bundle_binding':native['id']},
        'broad.event_timestamps':timestamp,
        'broad.honest_version_signal':{'evidence_complete':complete,'declared_version':'4','decoder_contract_version':'4',
            'incompatible_schema_distinguished':True,'matches_decoder_contract':True},
        'broad.observed_schema_stability':{'evidence_complete':complete,'advertised_contract':'dsh-native-v4',
            'observations':[{'build':build,'observed_on':collected_on,'decoder_contract':'dsh-native-v4','decoded':complete}],'exceptions':[]},
        'broad.stable_root_location':{'evidence_complete':root_repetitions is not None,'repetitions':root_repetitions or []},
        'broad.naive_reader_duplicate_safety':{'evidence_complete':complete,'event_ids':list(dict.fromkeys(x[0] for x in semantic_occurrences)),
            'forward_records':[{'event_id':identity,'occurrence_id':occurrence,'state':'active'} for identity,occurrence in semantic_occurrences],
            'deduplication':{'documented':True,'rule':'message ID; tool invocation identity is turn plus step plus callId; assistant tool-call blocks and tool/call occurrences remain in the naive denominator; an agent/inbox/spliced record with the queued prompt and a projection-cache unit with the exact message text are retained occurrences of that message; a tool/result record with all text arguments of its call restates the call'}},
        'broad.classified_content_density':{'evidence_complete':complete and cache is not None,'classification_rule':'logical-record-role-v1','records':density},
    }
    doc={'schema_version':FORMAT_EVIDENCE_SCHEMA_VERSION,'run_id':run_id,'configuration_id':'deepseek-harness-cli',
         'repetition':repetition,'build':build,'collected_on':collected_on,'result_id':'dsh-result:'+run_id,
         'observer':observer,'native_manifest':native_manifest,
         'profile':{'schema_version':'session-bench-format-profile-v1','run_id':run_id,'configuration_id':'deepseek-harness-cli',
                    'repetition':repetition,'broad_evidence':broad},
         'metric_evidence':[{'metric_id':metric,'observer_ids':[observer['id']],
                            'native_locators':[documentation if metric=='broad.documented_format' else native]} for metric in FORMAT_METRICS]}
    if cache is not None:
        next(row for row in doc['metric_evidence'] if row['metric_id']=='broad.classified_content_density')['native_locators'].append(cache['locator'])
    validate_format_evidence(doc)
    return doc
