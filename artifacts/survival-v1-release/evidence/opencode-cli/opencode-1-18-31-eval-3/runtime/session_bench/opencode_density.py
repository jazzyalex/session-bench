"""Canonical logical SQLite row accounting, including schema and metadata.

Every row in the closed copied database appears once. A row is encoded as its
complete column-name/value JSON object using the JSONL density canonical rule.
Native JSON stored in TEXT cells remains TEXT (it is not counted a second time).
WAL replay occurs only in a private clone. SQL schema rows also remain in the
denominator. BLOB cells and non-finite values are unsupported, never dropped.

The read is the tables the decoder opens: ``session``, ``message``, ``part``,
``event`` and the ``migration`` ledger. A text part states a message. A tool
part states its call once its state holds the arguments, and its result once it
ended, or once its ``metadata.output`` holds the complete final output of the call
(a running shell state does this): the status does not matter. A ``session``,
``message`` or ``event`` row that holds the exact text of a part restates that message. The ``event`` table is an update log. Each
``message.part.updated`` row holds a whole part, so it states again what the
``part`` row states. The first record of the read that states an event keeps its
role. A record that restates a stated event is a ``snapshot``. A record that
states no event is ``metadata``.
"""
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile

from .adapters.opencode_decoder import _bundle_files,_copy_bundle,_normal_path,_strict_json,OPENCODE_DB
from .native_density import NativeDensityInventory,_unresolved

READ_METADATA={'session','message','project','migration','__drizzle_migrations'}
MAX_ROWS=100_000
MAX_LOGICAL_BYTES=128*1024*1024


def _strings(value,found):
    """Every string value of a row, including strings inside JSON text cells."""
    if isinstance(value,str):
        found.add(value)
        if value[:1] in '{[':
            try:_strings(_strict_json(value,'density cell'),found)
            except (TypeError,ValueError):pass
    elif isinstance(value,dict):
        for item in value.values():_strings(item,found)
    elif isinstance(value,list):
        for item in value:_strings(item,found)
    return found


def part_statements(row,role,finals=None):
    """(events, message text) that one part states; ``role`` is the role of its message.

    ``row`` is a ``part`` row, or an event payload part with the same ``data`` JSON text.
    ``finals`` maps a call id to the final output of the call: a record that holds it states the result."""
    data=_strict_json(row['data'],'density part')
    if not isinstance(data,dict):return [],None
    identity=row.get('id') if isinstance(row.get('id'),str) and row.get('id') else None
    if data.get('type')=='text' and isinstance(data.get('text'),str) and data['text'] and role in {'user','assistant'} and identity:
        return [f'message:{identity}'],data['text']
    if data.get('type')=='tool' and isinstance(data.get('state'),dict) and identity:
        call=data.get('callID') if isinstance(data.get('callID'),str) and data.get('callID') else identity
        # A pending state has no arguments yet: it names the tool but does not restate the call.
        state=data['state'];metadata=state.get('metadata') if isinstance(state.get('metadata'),dict) else {}
        # A record that holds the full result output restates the result, whatever its status.
        full=state.get('status') in {'completed','error'} or (isinstance(metadata.get('output'),str) and metadata['output']!='' and metadata['output']==(finals or {}).get(call))
        return ([f'call:{call}'] if state.get('input') else [])+([f'result:{call}'] if full else []),None
    return [],None


def _role_kind(event,role):
    """Density role of the first record that states ``event``."""
    return {'call':'tool_call','result':'tool_result'}.get(event.split(':')[0]) or {'user':'user_message','assistant':'assistant_message'}.get(role,'unknown')


def _read_statements(con,session_id):
    """Occurrences of the read in forward order, the rows that restate a part text, and the density role of each ``event`` row."""
    roles={}
    for row in con.execute('SELECT id,data FROM message WHERE session_id=?',(session_id,)):
        value=_strict_json(row['data'],'density message');roles[row['id']]=value.get('role') if isinstance(value,dict) else None
    occurrences=[];texts={};stated=set();finals={}
    for row in con.execute('SELECT * FROM part WHERE session_id=?',(session_id,)):
        data=_strict_json(row['data'],'density part');state=data.get('state') if isinstance(data,dict) and data.get('type')=='tool' else None
        if isinstance(state,dict) and state.get('status') in {'completed','error'} and isinstance(state.get('output'),str) and state['output']:
            finals[data.get('callID') if isinstance(data.get('callID'),str) and data.get('callID') else row['id']]=state['output']
    for row in con.execute('SELECT * FROM part WHERE session_id=? ORDER BY rowid',(session_id,)):
        row=dict(row);events,text=part_statements(row,roles.get(row.get('message_id')),finals)
        occurrences+=[(event,f'part:{row["id"]}' if len(events)==1 else f'part:{row["id"]}:statement-{index}') for index,event in enumerate(events)]
        stated.update(events)
        if text is not None:texts.setdefault(text,f'message:{row["id"]}')
    restating={};kinds={}
    for table in ('session','message'):
        key='id' if table=='session' else 'session_id'
        for row in con.execute(f'SELECT * FROM "{table}" WHERE "{key}"=? ORDER BY rowid',(session_id,)):
            row=dict(row);found=sorted({texts[value] for value in _strings(list(row.values()),set()) if value in texts})
            if found:
                restating[(table,row['id'])]=found
                occurrences+=[(event,f'{table}:{row["id"]}' if len(found)==1 else f'{table}:{row["id"]}:statement-{index}') for index,event in enumerate(found)]
    # The update log, in its native order (seq). A part event holds a whole part.
    if con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='event'").fetchone():
        for row in con.execute('SELECT * FROM event WHERE aggregate_id=? ORDER BY seq',(session_id,)):
            row=dict(row);value=_strict_json(row['data'],'density event')
            part=value.get('part') if isinstance(value,dict) else None
            if isinstance(part,dict) and isinstance(row.get('type'),str) and row['type'].startswith('message.part.updated.'):
                events,_=part_statements({'id':part.get('id'),'data':json.dumps(part)},roles.get(part.get('messageID')),finals)
            else:
                events=sorted({texts[v] for v in _strings(list(row.values()),set()) if v in texts})
            occurrences+=[(event,f'event:{row["id"]}' if len(events)==1 else f'event:{row["id"]}:statement-{index}') for index,event in enumerate(events)]
            new=[event for event in events if event not in stated]
            stated.update(events)
            # The first record that states an event keeps its role; a repeat is a snapshot; no statement is metadata.
            kinds[row['id']]=_role_kind(new[-1],roles.get(part.get('messageID')) if isinstance(part,dict) else None) if new else 'snapshot' if events else 'metadata'
    return occurrences,restating,kinds


def opencode_forward_occurrences(bundle,*,session_id):
    """(event id, occurrence id) for every statement in the session, message, part and event tables."""
    root=_normal_path(bundle);declared=_bundle_files(root)
    with tempfile.TemporaryDirectory(prefix='session-bench-sqlite-forward-') as temp:
        clone=Path(temp);_copy_bundle(root,clone,declared=declared)
        con=sqlite3.connect(f'file:{clone/OPENCODE_DB}?mode=ro',uri=True);con.row_factory=sqlite3.Row
        try:
            con.execute('PRAGMA query_only=ON');con.execute('PRAGMA trusted_schema=OFF')
            return _read_statements(con,session_id)[0]
        except (TypeError,ValueError,KeyError,sqlite3.DatabaseError):
            raise ValueError('SQLite forward read unsupported or invalid') from None
        finally:con.close()


def build_opencode_density(bundle, *, manifest, session_id, complete_record_family=False):
    if complete_record_family is not True:return _unresolved('complete native family required')
    root=_normal_path(bundle);declared=_bundle_files(root)
    expected={r['name']:(r['sha256'],r['size_bytes']) for r in manifest['files']}
    if len(expected)!=len(manifest['files']):raise ValueError('duplicate SQLite manifest member')
    if set(expected)!={r.name for r in declared}:raise ValueError('SQLite density family differs from manifest')
    if any(expected[r.name]!=(r.sha256,r.size_bytes) for r in declared):raise ValueError('SQLite density physical hash mismatch')
    records=[];locators=[];total=0
    def add(table,ordinal,row,kind):
        nonlocal total
        if len(records)>=MAX_ROWS:raise ValueError('SQLite density record limit')
        # sqlite3 returns only scalar column values. A BLOB has no common
        # canonical JSON representation under this version of the metric.
        raw=json.dumps(row,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
        total+=len(raw)
        if total>MAX_LOGICAL_BYTES:raise ValueError('SQLite density logical byte limit')
        key=f'{table}:row-{ordinal}'
        records.append({'record_id':key,'record_kind':kind,'logical_bytes':len(raw),
                        'classification':'useful' if kind in {'user_message','assistant_message','tool_call','tool_result'} else 'unknown' if kind=='unknown' else 'unclassified'})
        locators.append({'id':key,'sha256':hashlib.sha256(raw).hexdigest()})
    with tempfile.TemporaryDirectory(prefix='session-bench-sqlite-density-') as temp:
        clone=Path(temp);_copy_bundle(root,clone,declared=declared)
        con=sqlite3.connect(f'file:{clone/OPENCODE_DB}?mode=ro',uri=True);con.row_factory=sqlite3.Row
        try:
            con.execute('PRAGMA query_only=ON');con.execute('PRAGMA trusted_schema=OFF')
            sessions=[r[0] for r in con.execute('SELECT id FROM session')]
            if sessions!=[session_id]:raise ValueError('SQLite density requires exactly the captured session')
            schema=list(con.execute('SELECT type,name,tbl_name,rootpage,sql FROM sqlite_master ORDER BY type,name'))
            for i,row in enumerate(schema):add('sqlite_master',i,dict(row),'metadata')
            roles={}
            for row in con.execute('SELECT id,data FROM message'):
                value=_strict_json(row['data'],'density message');roles[row['id']]=value.get('role') if isinstance(value,dict) else None
            restating,event_kinds=_read_statements(con,session_id)[1:]
            for table in sorted(r['name'] for r in schema if r['type']=='table'):
                escaped=table.replace('"','""')
                # Stable canonical ordering does not presume an INTEGER rowid.
                rows=[dict(r) for r in con.execute(f'SELECT * FROM "{escaped}" LIMIT {MAX_ROWS+1}')]
                if len(rows)>MAX_ROWS:raise ValueError('SQLite density table row limit')
                rows.sort(key=lambda r:json.dumps(r,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False))
                for i,row in enumerate(rows):
                    # A table the decoder opens is never unknown by its name.
                    kind='metadata' if table in READ_METADATA else 'unknown'
                    if table=='event':kind=event_kinds.get(row.get('id'),'metadata')
                    if table in {'session','message'} and (table,row.get('id')) in restating:kind='snapshot'
                    if table=='part':
                        data=_strict_json(row['data'],'density part')
                        if isinstance(data,dict):
                            part=data.get('type')
                            if part=='text' and isinstance(data.get('text'),str):
                                kind={'user':'user_message','assistant':'assistant_message'}.get(roles.get(row.get('message_id')),'unknown')
                            elif part=='tool' and isinstance(data.get('state'),dict):
                                kind='tool_result' if data['state'].get('status') in {'completed','error'} else 'tool_call'
                            elif part in {'step-start','step-finish'}:kind='metadata'
                    add(table,i,row,kind)
        except (TypeError,ValueError,sqlite3.DatabaseError):
            raise ValueError('SQLite native logical inventory unsupported or invalid') from None
        finally:con.close()
    return NativeDensityInventory({'evidence_complete':True,'classification_rule':'logical-record-role-v1','records':records},tuple(locators),())
