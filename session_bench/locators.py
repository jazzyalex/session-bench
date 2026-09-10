"""Validate reconstructed source references against immutable artifact bytes."""
import json
from pathlib import Path
import sqlite3
import tempfile

from .bundle import canonical, digest, safe_read, safe_path


def validate_sources(root, manifest, decoded):
    inventory={a['id']:a for a in manifest['artifacts'] if a['role']=='native'}
    cached={}
    def data(aid):
        if aid not in inventory:
            raise ValueError('invalid decoder source locator: unknown native artifact')
        if aid not in cached:
            cached[aid]=safe_read(root,inventory[aid]['path'])
        if digest(cached[aid])!=inventory[aid]['sha256']:
            raise ValueError('native source changed before locator verification')
        return cached[aid]
    sqlite_rows={}
    jsonl_lines={}
    for event in decoded['events']:
        loc=event.get('locator',{})
        artifact=inventory.get(loc.get('artifact_id'))
        if not artifact or loc.get('sha256')!=artifact['sha256']:
            raise ValueError('invalid decoder source locator: artifact digest/identity')
        content=data(artifact['id'])
        if 'line' in loc:
            line,start,end=loc.get('line'),loc.get('byte_start'),loc.get('byte_end')
            if any(type(x) is not int for x in (line,start,end)):
                raise ValueError('invalid decoder source locator: JSONL coordinates')
            # JSONL decoder splits only LF; bytes.splitlines would treat raw VT
            # as a record separator even though JSON strings may be malformed.
            if artifact['id'] not in jsonl_lines:
                pieces=content.split(b'\n')
                lines=[p+b'\n' for p in pieces[:-1]]+([pieces[-1]] if pieces[-1] else [])
                positions=[];offset=0
                for raw in lines:
                    positions.append((offset,offset+len(raw)));offset+=len(raw)
                jsonl_lines[artifact['id']]=(lines,positions)
            lines,positions=jsonl_lines[artifact['id']]
            if not 1<=line<=len(lines) or (start,end)!=positions[line-1]:
                raise ValueError('invalid decoder source locator: JSONL range')
            payload=content[start:end].rstrip(b'\r\n')
        elif loc.get('table')=='events' and loc.get('column')=='payload' and type(loc.get('row_key')) is int:
            aid=artifact['id']
            if aid not in sqlite_rows:
                with tempfile.TemporaryDirectory(prefix='sb-locator-') as directory:
                    clone=Path(directory)
                    # Preserve physical companion placement; do not flatten names.
                    for a in inventory.values():
                        relative=a['path'].removeprefix('native/')
                        dest=safe_path(clone,relative)
                        dest.parent.mkdir(parents=True,exist_ok=True)
                        dest.write_bytes(data(a['id']))
                    db=clone/artifact['path'].removeprefix('native/')
                    connection=sqlite3.connect(db.as_uri()+'?mode=ro',uri=True)
                    try:
                        connection.execute('PRAGMA trusted_schema=OFF')
                        connection.execute('PRAGMA query_only=ON')
                        objects=connection.execute("SELECT type,name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'").fetchall()
                        if len(objects)!=1 or objects[0][0:2]!=('table','events') or 'VIRTUAL' in objects[0][2].upper():
                            raise ValueError('invalid SQLite source schema for locator')
                        info=connection.execute('PRAGMA table_info(events)').fetchall()
                        if len(info)!=2 or [(x[1],x[2].upper(),x[5]) for x in info]!=[('row_key','INTEGER',1),('payload','TEXT',0)]:
                            raise ValueError('invalid SQLite source columns for locator')
                        sqlite_rows[aid]={key:value for key,value in connection.execute('SELECT row_key,payload FROM events')}
                    finally:
                        connection.close()
            value=sqlite_rows[aid].get(loc['row_key'])
            if not isinstance(value,str):
                raise ValueError('invalid decoder source locator: SQLite row')
            payload=value.encode('utf-8')
            companion_paths={artifact['path']+'-wal',artifact['path']+'-shm'}
            expected_companions=sorted(a['sha256'] for a in inventory.values() if a['path'] in companion_paths)
            if sorted(loc.get('dependency_sha256',[]))!=expected_companions:
                raise ValueError('invalid decoder source locator: companion digest')
        else:
            raise ValueError('invalid decoder source locator: unsupported locator')
        if digest(payload)!=loc.get('record_sha256'):
            raise ValueError('invalid decoder source locator: record digest')
        native=json.loads(payload)
        normalized={k:event[k] for k in ('id','session_id','kind','fields')}
        native['id']=native['id'] or None
        if canonical(native)!=canonical(normalized):
            raise ValueError('decoder values do not match referenced native record')
