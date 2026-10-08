"""Native Antigravity step transcripts; no observer facts enter this decoder.

Step order binds messages to submitted turns. GENERIC result records do not
expose call IDs: they are retained without fabricating action-result links.
"""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import re
import shlex

from .dsh_live import strict_json
from .adapters.agent_session_native_decoder import decode_agent_native_bytes

# A whole-file write is an edit of its target, like the two replace tools.
EDIT_TOOLS=frozenset({'replace_file_content','multi_replace_file_content','write_to_file'})
TOOL_NAMES={'run_command':'bash',**{name:'edit' for name in EDIT_TOOLS}}


def _arguments(raw):
    if not isinstance(raw,dict):raise ValueError('Antigravity tool args must be an object')
    result={}
    for key,value in raw.items():
        if not isinstance(value,str):raise ValueError('Antigravity args must retain serialized JSON values')
        result[key]=strict_json(value)
    return result


def _completed_file_changes(records):
    """Hash exact native full-file reads and completed full-file diffs.

    This deliberately supports only diffs covering a byte-verified complete
    read. Tool arguments describe intent and never supply successful edits.
    GENERIC records identify the changed path, not an action or call ID.
    """
    snapshots = {}
    changes = []
    for record in records:
        row = record['raw']
        if row.get('type') != 'GENERIC' or row.get('status') != 'DONE':
            continue
        content = row.get('content', '')
        if not isinstance(content, str):
            continue
        read = re.search(
            r'\nFile Path: `file://([^\n`]+)`\nTotal Lines: (\d+)\n'
            r'Total Bytes: (\d+)\nShowing lines 1 to (\d+)\n[^\n]+\n'
            r'(.*?)\nThe above content shows the entire, complete file contents of the requested file\.\n',
            content, re.DOTALL)
        if read:
            target, total, size, last, numbered = read.groups()
            lines = numbered.split('\n')
            if int(total) != int(last) or len(lines) != int(total):
                continue
            prefixes = [f'{index}: ' for index in range(1, len(lines) + 1)]
            if not all(line.startswith(prefix) for line, prefix in zip(lines, prefixes)):
                continue
            text = '\n'.join(line[len(prefix):] for line, prefix in zip(lines, prefixes))
            # Native line views can include a terminal empty split segment.
            # The declared byte count selects the exact newline convention.
            candidates = [value for value in (text, text + '\n')
                          if len(value.encode('utf-8')) == int(size)]
            if len(candidates) == 1:
                snapshots[target] = (candidates[0], record['locator'])
            continue
        edit = re.search(
            r'\nThe following changes were made by the (?:replace_file_content|multi_replace_file_content) tool to: '
            r'([^\n]+?)\. If relevant,[^\n]*\n\[diff_block_start\]\n'
            r'@@ -1,(\d+) \+1,(\d+) @@\n(.*?)\n\[diff_block_end\]',
            content, re.DOTALL)
        if not edit:
            continue
        target, old_count, new_count, diff = edit.groups()
        snapshot = snapshots.get(target)
        if snapshot is None:
            continue
        lines = diff.split('\n')
        if not lines or any(not line or line[0] not in ' +-' for line in lines):
            continue
        old = [line[1:] for line in lines if line[0] in ' -']
        new = [line[1:] for line in lines if line[0] in ' +']
        preimages = [(suffix, '\n'.join(old) + suffix) for suffix in ('', '\n')
                     if '\n'.join(old) + suffix == snapshot[0]]
        if len(old) != int(old_count) or len(new) != int(new_count) or len(preimages) != 1:
            continue
        suffix, before = preimages[0]
        after = '\n'.join(new) + suffix
        relative = 'fixture_project/' + target.split('/fixture_project/', 1)[1] if '/fixture_project/' in target else target
        changes.append({'id': f"step:{row['step_index']}:file-change", 'path': relative,
                        'before_sha256': hashlib.sha256(before.encode('utf-8')).hexdigest(),
                        'after_sha256': hashlib.sha256(after.encode('utf-8')).hexdigest(),
                        'sequence': row['step_index'], 'locator': record['locator'],
                        'preimage_locator': snapshot[1],
                        'method': 'native byte-verified complete file read and completed full-file diff'})
        snapshots[target] = (after, record['locator'])
    return changes


def decode_antigravity_native(path):
    path=Path(path)
    if path.is_symlink() or path.stat().st_size>64*1024*1024:raise ValueError('unsafe native artifact')
    raw=path.read_bytes()
    inspected=decode_agent_native_bytes('antigravity',raw)
    rows=[strict_json(line) for line in raw.splitlines() if line.strip()]
    out={k:[] for k in ('turns','responses','actions','results','relations','usage','file_changes','records')}
    out.update(format='antigravity-step-jsonl-v1',status='ok',diagnostics=[],physical_sha256=hashlib.sha256(raw).hexdigest())
    if inspected.status!='complete':
        out['status']='unsupported';out['diagnostics'].append({'code':'unsupported_native_inventory'})
    turn=None;prior=-1
    for line,row in enumerate(rows,1):
        step=row.get('step_index')
        if type(step)is not int or step<=prior:raise ValueError('native steps must be unique increasing integers')
        prior=step
        loc={'line':line,'step_index':step,'artifact':path.name,'physical_sha256':out['physical_sha256']}
        out['records'].append({'raw':row,'locator':loc})
        if row.get('status')!='DONE':
            out['status']='unsupported';out['diagnostics'].append({'code':'incomplete_native_step','locator':loc})
        kind=row.get('type')
        if kind=='USER_INPUT':
            if not isinstance(row.get('content'),str):raise ValueError('native user text missing')
            text=row['content']
            if text.startswith('<USER_REQUEST>\n'):
                if text.count('<USER_REQUEST>')!=1 or text.count('</USER_REQUEST>')!=1:
                    raise ValueError('ambiguous native user request wrapper')
                text,metadata=text[len('<USER_REQUEST>\n'):].split('</USER_REQUEST>',1)
                text=text.removesuffix('\n')
                if metadata.strip() and not metadata.lstrip().startswith('<ADDITIONAL_METADATA>'):
                    raise ValueError('unsupported native request suffix')
            turn=f'step:{step}'
            out['turns'].append({'id':turn,'role':'user','text':text,'sequence':step,'locator':loc})
        elif kind=='PLANNER_RESPONSE':
            if turn is None:raise ValueError('assistant precedes submitted turn')
            if row.get('content'):
                markers=re.findall(r'SB_SURVIVAL_V1_RESPONSE_[^\s]+',row['content'])
                if len(markers)>1:raise ValueError('ambiguous native response canary')
                out['responses'].append({'id':f'step:{step}','role':'assistant','text':row['content'],
                    'turn_id':turn,'sequence':step,'status':'completed','locator':loc})
                if markers and row['content'].rstrip().endswith(markers[0]):
                    out['responses'][-1]['canary']=markers[0]
                out['relations'].append({'kind':'turn_response','from_id':turn,'to_id':f'step:{step}',
                    'locator':loc,'method':'native ordered steps since latest USER_INPUT'})
            for index,call in enumerate(row.get('tool_calls',[])):
                args=_arguments(call.get('args'))
                name={'run_command':'bash','replace_file_content':'edit','multi_replace_file_content':'edit'}.get(call.get('name'),call.get('name'))
                action={'id':f'step:{step}:call:{index}','name':name,'tool_name':name,'native_name':call.get('name'),
                        'input':args,'turn_id':turn,'sequence':step,'locator':loc}
                command=args.get('CommandLine')
                if isinstance(command,str):
                    action.update(command=command,argv=shlex.split(command))
                    if re.fullmatch(r'python3 bench_check\.py (?:inspect|baseline|final) --run-canary [A-Za-z0-9_-]+',command):
                        action['target']='fixture_project/checkout.py'
                target=args.get('TargetFile',args.get('AbsolutePath'))
                if isinstance(target,str):
                    action['target']='fixture_project/'+target.split('/fixture_project/',1)[1] if '/fixture_project/' in target else target
                cwd=args.get('Cwd')
                if isinstance(cwd,str):action['cwd']='fixture_project' if cwd.endswith('/fixture_project') else cwd
                out['actions'].append(action)
        elif kind=='GENERIC':
            content=row.get('content')
            if not isinstance(content,str):raise ValueError('native generic content missing')
            result={'id':f'step:{step}','output':content,'sequence':step,'turn_id':turn,'locator':loc}
            codes=re.findall(r'The command exited with code (-?\d+)\.',content)
            if len(codes)==1:
                result['exit_code']=int(codes[0]);result['status']='success' if int(codes[0])==0 else 'failure'
            nonce=re.search(r'SB_SURVIVAL_V1_HELPER_(?:INSPECT|BASELINE|FINAL)_([^\s]+)',content)
            if nonce:result['helper_nonce']=nonce.group(1)
            out['results'].append(result)
    out['file_changes'] = _completed_file_changes(out['records'])
    return out


def stdout_projection(raw, helper_rows, *, step_text=False):
    """Build observer projection only from stdout plus separate helper receipts.

    Token totals are turn/session aggregates and never substituted for final
    response usage. Tool arguments omitted by stdout remain omitted.

    With ``step_text`` each streamed text step is one text row, in stream
    order. The stream ends the text of a step with one line feed; exactly that
    one is removed. The step texts must rebuild the final ``response``.
    """
    rows=[strict_json(line) for line in raw.splitlines() if line.strip()]
    initial=[r['conversation_id'] for r in rows if r.get('event')=='init']
    finals=[r['result'] for r in rows if r.get('event')=='result']
    if len(initial)!=1 or len(finals)!=1 or finals[0].get('conversation_id')!=initial[0] or finals[0].get('status')!='SUCCESS':
        raise ValueError('stdout lacks one successful session boundary')
    session=initial[0];out=[];seen=set();deltas={};texts=[]
    for row in rows:
        step=row.get('step_update',{})
        if step.get('conversation_id') not in (None,session):raise ValueError('cross-session stdout step')
        if step_text and step.get('step_type')=='agent_response':
            index=step.get('step_index')
            if isinstance(step.get('text_delta'),str):deltas.setdefault(index,[]).append(step['text_delta'])
            if step.get('state')=='DONE' and index in deltas:
                text=''.join(deltas.pop(index))
                if not text.endswith('\n'):raise ValueError('stdout step text lacks its line terminator')
                texts.append(text)
                if text[:-1]:out.append({'type':'text','sessionID':session,'text':text[:-1]})
        info=step.get('tool_info')
        if not isinstance(info,dict) or step.get('state')!='DONE':continue
        ident=step.get('step_index')
        if type(ident)is not int or ident in seen:raise ValueError('duplicate stdout tool completion')
        seen.add(ident)
        params=info.get('parameters',{});name=info.get('name');output=info.get('output','')
        args={};exit_code=None
        if name=='run_command':
            args={'command':params.get('CommandLine')}
            matched=[r for r in helper_rows if r.get('output') and r['output'].strip() in output]
            if len(matched)==1:exit_code=matched[0]['exit_code']
        elif name in EDIT_TOOLS:
            args={'filePath':params.get('TargetFile')}
        elif name=='view_file':args={'filePath':params.get('AbsolutePath')}
        else:args=dict(params)
        out.append({'type':'tool_use','sessionID':session,'callID':f'stdout-step:{ident}',
                    'tool':TOOL_NAMES.get(name,name),
                    'input':args,'state':{'status':'completed','output':output,'metadata':{'exit':exit_code}}})
    if step_text:
        if deltas or ''.join(texts)!=finals[0].get('response',''):raise ValueError('stdout step text differs from the final response')
    else:
        out.append({'type':'text','sessionID':session,'text':finals[0].get('response','')})
    return {'session_id':session,'jsonl':'\n'.join(json.dumps(r,ensure_ascii=False) for r in out)+'\n'}


# --- Complete session directory (agy 1.2.16), contract ``antigravity-step-jsonl-v2`` ---
FAMILY_FORMAT='antigravity-step-jsonl-v2'
STEP_SOURCES=frozenset({'USER_EXPLICIT','MODEL','SYSTEM'})
STEP_COMMON_KEYS=frozenset({'step_index','source','type','status','created_at'})
STEP_KEYS={'USER_INPUT':frozenset({'content'}),'SYSTEM_MESSAGE':frozenset({'content'}),'GENERIC':frozenset({'content'}),
           'PLANNER_RESPONSE':frozenset({'content','thinking','tool_calls','input_tokens','cache_read_tokens','output_tokens'})}
USAGE_KEYS=('input_tokens','cache_read_tokens','output_tokens')
RESULT_HEADER=re.compile(r'Created At: [^\n]+\nCompleted At: [^\n]+\n')
_CREATED_AT=re.compile(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z')
_COMMAND_RESULT=re.compile(r'\nThe command exited with code (-?\d+)\.\nOutput:\n(.*)\n\Z',re.DOTALL)
_EDIT_DONE=re.compile(r'The following changes were made by the (replace_file_content|multi_replace_file_content) tool to: ([^\n]+?)\. If relevant,')
_WRITE_DONE=re.compile(r'Created file file://([^\n]+?) with requested content\.(?:\n|\Z)')
_COMPLETE_READ=re.compile(
    r'File Path: `file://([^\n`]+)`\nTotal Lines: (\d+)\nTotal Bytes: (\d+)\nShowing lines 1 to (\d+)\n[^\n]+\n'
    r'(.*?)\nThe above content shows the entire, complete file contents of the requested file\.\n',re.DOTALL)
_HELPER_COMMAND=re.compile(r'python3 bench_check\.py (inspect|baseline|final) --run-canary [A-Za-z0-9_-]+')


def _relative_target(target):
    return 'fixture_project/'+target.split('/fixture_project/',1)[1] if '/fixture_project/' in target else target


def step_contract_exceptions(row):
    """Codes of everything in one step record that the v2 contract does not name."""
    kind=row.get('type')
    if kind not in STEP_KEYS:return ['unknown_step_type']
    codes=[]
    if set(row)-STEP_COMMON_KEYS-STEP_KEYS[kind] or STEP_COMMON_KEYS-set(row):codes.append('unknown_step_key')
    if row.get('source') not in STEP_SOURCES:codes.append('unknown_step_source')
    if row.get('status')!='DONE':codes.append('incomplete_native_step')
    if not isinstance(row.get('created_at'),str) or not _CREATED_AT.fullmatch(row['created_at']):codes.append('unknown_step_timestamp')
    calls=row.get('tool_calls',[])
    if not isinstance(calls,list) or any(not isinstance(call,dict) or set(call)!={'name','args'} or not isinstance(call['name'],str)
                                         or not isinstance(call['args'],dict) for call in calls):
        codes.append('unknown_tool_call_shape')
    return codes


def complete_file_read(content):
    """(absolute path, exact text) of a byte-verified complete file read, else None."""
    read=_COMPLETE_READ.search(content)
    if not read:return None
    target,total,size,last,numbered=read.groups()
    lines=numbered.split('\n')
    if int(total)!=int(last) or len(lines)!=int(total):return None
    prefixes=[f'{index}: ' for index in range(1,len(lines)+1)]
    if not all(line.startswith(prefix) for line,prefix in zip(lines,prefixes)):return None
    text='\n'.join(line[len(prefix):] for line,prefix in zip(lines,prefixes))
    candidates=[value for value in (text,text+'\n') if len(value.encode('utf-8'))==int(size)]
    return (target,candidates[0]) if len(candidates)==1 else None


def _whole_file_write_changes(records):
    """Hashes of a whole-file write: native complete read, one write call, its native completion.

    The completion step says ``Created file file://<path> with requested
    content.`` The requested content is the ``CodeContent`` of the one
    ``write_to_file`` call (``Overwrite`` true) that names the same path since
    the complete read. Any other edit call on that path in between gives no hashes.
    """
    snapshots,calls,changes={}, [], []
    for record in records:
        row=record['raw']
        if row.get('type')=='PLANNER_RESPONSE':
            for call in row.get('tool_calls',[]) if isinstance(row.get('tool_calls'),list) else []:
                if isinstance(call,dict) and call.get('name') in EDIT_TOOLS and isinstance(call.get('args'),dict):
                    args=_arguments(call['args'])
                    calls.append((args.get('TargetFile'),call['name'],args,record['locator']))
            continue
        if row.get('type')!='GENERIC' or row.get('status')!='DONE' or not isinstance(row.get('content'),str):continue
        read=complete_file_read(row['content'])
        if read:
            snapshots[read[0]]=(read[1],record['locator']);calls=[item for item in calls if item[0]!=read[0]]
            continue
        done=_WRITE_DONE.search(row['content'])
        if not done:continue
        target=done.group(1)
        mine=[item for item in calls if item[0]==target];calls=[item for item in calls if item[0]!=target]
        before=snapshots.pop(target,None)
        if before is None or len(mine)!=1:continue
        _,name,args,call_locator=mine[0]
        if name!='write_to_file' or args.get('Overwrite') is not True or not isinstance(args.get('CodeContent'),str):continue
        after=args['CodeContent']
        changes.append({'id':f"step:{row['step_index']}:file-change",'path':_relative_target(target),
                        'before_sha256':hashlib.sha256(before[0].encode('utf-8')).hexdigest(),
                        'after_sha256':hashlib.sha256(after.encode('utf-8')).hexdigest(),
                        'sequence':row['step_index'],'locator':record['locator'],'preimage_locator':before[1],
                        'edit_locator':call_locator,'hash_source':'native_preimage_and_whole_file_write',
                        'method':'native byte-verified complete file read, one whole-file write call and its native completion'})
        snapshots[target]=(after,record['locator'])
    return changes


def decode_antigravity_family(root,primary):
    """Decode the primary transcript of one copied session directory; no observer fact enters.

    ``root`` holds the copied directory and ``primary`` is
    ``<conversation-id>/.system_generated/logs/transcript.jsonl``. A result is
    its own step (``step:N``). The native bytes hold no key that links a
    result step to the tool call that caused it, so no action-result relation
    and no ``call_id`` is produced.
    """
    path=Path(root)/primary
    if path.is_symlink() or path.stat().st_size>64*1024*1024:raise ValueError('unsafe native artifact')
    raw=path.read_bytes()
    rows=[strict_json(line) for line in raw.splitlines() if line.strip()]
    out={k:[] for k in ('turns','responses','actions','results','relations','usage','file_changes','records')}
    out.update(format=FAMILY_FORMAT,status='ok',diagnostics=[],physical_sha256=hashlib.sha256(raw).hexdigest(),
               session_id=primary.split('/',1)[0])
    turn=None;prior=-1
    for line,row in enumerate(rows,1):
        step=row.get('step_index') if isinstance(row,dict) else None
        if type(step)is not int or step<=prior:raise ValueError('native steps must be unique increasing integers')
        prior=step
        loc={'line':line,'step_index':step,'artifact':primary,'physical_sha256':out['physical_sha256']}
        stamp=row.get('created_at')
        out['records'].append({'raw':row,'locator':loc,'timestamp':stamp})
        codes=step_contract_exceptions(row)
        for code in codes:out['diagnostics'].append({'code':code,'locator':loc})
        if codes:
            out['status']='unsupported'
            continue
        kind=row['type']
        if kind=='USER_INPUT':
            text=row.get('content')
            if not isinstance(text,str):raise ValueError('native user text missing')
            if text.startswith('<USER_REQUEST>\n'):
                if text.count('<USER_REQUEST>')!=1 or text.count('</USER_REQUEST>')!=1:
                    raise ValueError('ambiguous native user request wrapper')
                text,metadata=text[len('<USER_REQUEST>\n'):].split('</USER_REQUEST>',1)
                text=text.removesuffix('\n')
                if metadata.strip() and not metadata.lstrip().startswith('<ADDITIONAL_METADATA>'):
                    raise ValueError('unsupported native request suffix')
            turn=f'step:{step}'
            out['turns'].append({'id':turn,'role':'user','text':text,'sequence':step,'locator':loc,'timestamp':stamp})
        elif kind=='PLANNER_RESPONSE':
            if turn is None:raise ValueError('assistant precedes submitted turn')
            calls=row.get('tool_calls',[])
            if isinstance(row.get('content'),str) and row['content']:
                markers=re.findall(r'SB_SURVIVAL_V1_RESPONSE_[^\s]+',row['content'])
                if len(markers)>1:raise ValueError('ambiguous native response canary')
                # A step that also requests a tool is not the end of the turn.
                response={'id':f'step:{step}','role':'assistant','text':row['content'],'turn_id':turn,'sequence':step,
                          'status':'completed','phase':'commentary' if calls else 'final_answer','locator':loc,'timestamp':stamp}
                if markers and row['content'].rstrip().endswith(markers[0]):response['canary']=markers[0]
                fragment={key:row[key] for key in USAGE_KEYS if type(row.get(key)) is int}
                if fragment:response['usage']=fragment
                out['responses'].append(response)
                if not calls:
                    out['relations'].append({'kind':'turn_response','from_id':turn,'to_id':f'step:{step}',
                        'locator':loc,'method':'native ordered steps since latest USER_INPUT'})
            for index,call in enumerate(calls):
                args=_arguments(call['args'])
                name=TOOL_NAMES.get(call['name'],call['name'])
                action={'id':f'step:{step}:call:{index}','name':name,'tool_name':name,'native_name':call['name'],
                        'input':args,'turn_id':turn,'sequence':step,'locator':loc,'timestamp':stamp}
                command=args.get('CommandLine')
                if isinstance(command,str):
                    action.update(command=command,argv=shlex.split(command))
                    if _HELPER_COMMAND.fullmatch(command):action['target']='fixture_project/checkout.py'
                target=args.get('TargetFile',args.get('AbsolutePath'))
                if isinstance(target,str):action['target']=_relative_target(target)
                cwd=args.get('Cwd')
                if isinstance(cwd,str):action['cwd']='fixture_project' if cwd.endswith('/fixture_project') else cwd
                out['actions'].append(action)
        elif kind=='GENERIC':
            content=row.get('content')
            if not isinstance(content,str):raise ValueError('native generic content missing')
            result={'id':f'step:{step}','output':content,'sequence':step,'turn_id':turn,'locator':loc,'timestamp':stamp}
            header=RESULT_HEADER.match(content)
            body=content[header.end():] if header else content
            command=_COMMAND_RESULT.fullmatch(body)
            edit=_EDIT_DONE.match(body);write=_WRITE_DONE.match(body)
            if header is None:
                out['status']='unsupported';out['diagnostics'].append({'code':'unknown_result_shape','locator':loc})
            elif command:
                # Declared text transform: the text after ``Output:``, without the one line feed the template adds.
                code=int(command.group(1))
                result.update(output=command.group(2),exit_code=code,status='success' if code==0 else 'failure')
                nonces=re.findall(r'SB_SURVIVAL_V1_HELPER_(?:INSPECT|BASELINE|FINAL)_([^\s]+)',command.group(2))
                if len(nonces)==1:result['helper_nonce']=nonces[0]
            elif edit or write:
                # A completed structured edit is a success; it has no process exit code of its own.
                result.update(output=body,status='success',exit_code=0,native_tool=edit.group(1) if edit else 'write_to_file',
                              target=_relative_target(edit.group(2) if edit else write.group(1)))
            else:
                result['output']=body
                read=complete_file_read(content)
                if read:result.update(native_tool='view_file',target=_relative_target(read[0]))
            out['results'].append(result)
    if out['status']=='ok':
        changes=[{**change,'hash_source':'native_preimage_and_native_diff'} for change in _completed_file_changes(out['records'])]
        changes+=_whole_file_write_changes(out['records'])
        stamps={record['locator']['step_index']:record['timestamp'] for record in out['records']}
        out['file_changes']=[{**change,'timestamp':stamps[change['sequence']]} for change in sorted(changes,key=lambda item:item['sequence'])]
    return out


# --- Whole-state capture: the conversation database is the primary record, contract ``antigravity-conversation-db-v1`` ---
STATE_FORMAT='antigravity-conversation-db-v1'


def conversation_db_path(session_id):
    return f'conversations/{session_id}.db'


def decode_antigravity_conversation(root,session_id,store=None):
    """Decode ``conversations/<id>.db`` of one copied whole-state family; no observer fact enters.

    Every fact comes from the database rows. A tool step names its call by the
    native call id (step metadata field 4.1 = the id in the model step's tool
    call), so an action-result relation is produced only from that key. The
    transcript files under ``brain/<id>/`` are mirrors and are not read here.
    """
    from .antigravity_conversation_db import (STEP_MODEL,STEP_SYSTEM,STEP_TOOL,STEP_USER,contract_exceptions,field,generation_rows,
                                               one,read_conversation_db,text,tool_calls)
    name=conversation_db_path(session_id)
    path=Path(root)/name
    if store is None:
        if path.is_symlink() or path.stat().st_size>64*1024*1024:raise ValueError('unsafe native artifact')
        store=read_conversation_db(path.read_bytes())
    out={k:[] for k in ('turns','responses','actions','results','relations','usage','file_changes','records')}
    out.update(format=STATE_FORMAT,status='ok',diagnostics=contract_exceptions(store,session_id),physical_sha256=store['physical_sha256'],
               session_id=session_id,user_version=store['user_version'])
    if out['diagnostics']:
        out['status']='unsupported'
    generations=generation_rows(store)
    turn=None;calls={};pseudo=[]
    for line,row in enumerate(store['tables'].get('steps',[]),1):
        step,kind,payload,metadata=row.get('idx'),row.get('step_type'),row.get('step_payload'),row.get('metadata')
        loc={'line':line,'step_index':step,'artifact':name,'table':'steps','physical_sha256':out['physical_sha256']}
        seconds=one(metadata,1,1,kind=int) if isinstance(metadata,bytes) else None
        out['records'].append({'raw':{'idx':step,'step_type':kind,'status':row.get('status'),'step_format':row.get('step_format')},
                               'locator':loc,'timestamp':seconds,'timestamp_nanos':one(metadata,1,2,kind=int) if isinstance(metadata,bytes) else None})
        if out['status']!='ok' or type(step)is not int:continue
        if kind==STEP_USER:
            turn=f'step:{step}'
            out['turns'].append({'id':turn,'role':'user','text':text(payload,19,2),'sequence':step,'locator':loc,'timestamp':seconds})
        elif kind==STEP_MODEL:
            if turn is None:raise ValueError('assistant precedes submitted turn')
            requested=tool_calls(payload);content=text(payload,20,1);response_id=text(payload,20,6)
            if content:
                markers=re.findall(r'SB_SURVIVAL_V1_RESPONSE_[^\s]+',content)
                if len(markers)>1:raise ValueError('ambiguous native response canary')
                response={'id':f'step:{step}','role':'assistant','text':content,'turn_id':turn,'sequence':step,'status':'completed',
                          'phase':'commentary' if requested else 'final_answer','locator':loc,'timestamp':seconds,'native_response_id':response_id}
                if markers and content.rstrip().endswith(markers[0]):response['canary']=markers[0]
                # Native counts keep their own keys. A count that the record does not hold is not added as zero.
                usage={key:one(metadata,9,number,kind=int) for key,number in (('input_tokens',2),('output_tokens',3),('cache_read_tokens',5))}
                usage={key:value for key,value in usage.items() if value is not None}
                if usage and text(metadata,9,7)==response_id:response['usage']=usage
                generation=generations.get(response_id)
                if generation and generation['model'] and generation['model_enum']:
                    response.update(model_id=generation['model'],configuration=generation['model_enum'],
                                    model_locator={'artifact':name,'table':'gen_metadata','row':generation['row']})
                out['responses'].append(response)
                if not requested:
                    out['relations'].append({'kind':'turn_response','from_id':turn,'to_id':f'step:{step}','locator':loc,
                                             'method':'native ordered steps since latest user step'})
            encoded=[]
            for index,(call_id,native_name,args) in enumerate(requested):
                tool=TOOL_NAMES.get(native_name,native_name)
                action={'id':f'step:{step}:call:{index}','call_id':call_id,'name':tool,'tool_name':tool,'native_name':native_name,
                        'input':args,'turn_id':turn,'sequence':step,'locator':loc,'timestamp':seconds}
                command=args.get('CommandLine')
                if isinstance(command,str):
                    action.update(command=command,argv=shlex.split(command))
                    if _HELPER_COMMAND.fullmatch(command):action['target']='fixture_project/checkout.py'
                target=args.get('TargetFile',args.get('AbsolutePath'))
                if isinstance(target,str):action['target']=_relative_target(target)
                cwd=args.get('Cwd')
                if isinstance(cwd,str):action['cwd']='fixture_project' if cwd.endswith('/fixture_project') else cwd
                out['actions'].append(action)
                calls.setdefault(call_id,[]).append(action)
                encoded.append({'name':native_name,'args':{key:json.dumps(value) for key,value in args.items()}})
            pseudo.append({'raw':{'type':'PLANNER_RESPONSE','status':'DONE','step_index':step,'tool_calls':encoded},'locator':loc})
        elif kind==STEP_TOOL:
            body=text(payload,140,2,1);call_id=text(metadata,4,1)
            result={'id':f'step:{step}','call_id':call_id,'output':body,'sequence':step,'turn_id':turn,'locator':loc,'timestamp':seconds}
            command=_COMMAND_RESULT.fullmatch(body)
            edit=_EDIT_DONE.match(body);write=_WRITE_DONE.match(body)
            if command:
                code=int(command.group(1))
                result.update(output=command.group(2),exit_code=code,status='success' if code==0 else 'failure')
                nonces=re.findall(r'SB_SURVIVAL_V1_HELPER_(?:INSPECT|BASELINE|FINAL)_([^\s]+)',command.group(2))
                if len(nonces)==1:result['helper_nonce']=nonces[0]
            elif edit or write:
                result.update(status='success',exit_code=0,native_tool=edit.group(1) if edit else 'write_to_file',
                              target=_relative_target(edit.group(2) if edit else write.group(1)))
            else:
                read=complete_file_read(body)
                if read:result.update(native_tool='view_file',target=_relative_target(read[0]))
            out['results'].append(result)
            # The call id is the native key of the relation. It must name exactly one call.
            if len(calls.get(call_id,[]))==1:
                out['relations'].append({'kind':'action_result','from_id':calls[call_id][0]['id'],'to_id':result['id'],'call_id':call_id,
                                         'locator':loc,'method':'tool step metadata call id equals the model step tool call id'})
            pseudo.append({'raw':{'type':'GENERIC','status':'DONE','step_index':step,'content':'Created At: -\nCompleted At: -\n'+body},'locator':loc})
        elif kind!=STEP_SYSTEM:
            raise ValueError('unknown step type passed the contract')
    if out['status']=='ok':
        changes=[{**change,'hash_source':'native_preimage_and_native_diff'} for change in _completed_file_changes(pseudo)]
        changes+=_whole_file_write_changes(pseudo)
        stamps={record['locator']['step_index']:record['timestamp'] for record in out['records']}
        out['file_changes']=[{**change,'timestamp':stamps[change['sequence']]} for change in sorted(changes,key=lambda item:item['sequence'])]
    return out
