import copy
import json
from pathlib import Path
import pytest
from session_bench.opencode_density import build_opencode_density
from session_bench.opencode_format_evidence import build_opencode_format_evidence
from session_bench.v1_public_score import validate_format_evidence

ROOT=Path(__file__).resolve().parents[1]
PACKAGE=ROOT/'artifacts/survival-v1-runs/opencode-cli-eval-1/evaluation-correction'

def manifest():return json.loads((PACKAGE/'native-manifest.json').read_text())
def build(value=None,**kwargs):
    m=manifest() if value is None else value
    return build_opencode_density(PACKAGE/'native-bundle',manifest=m,session_id=m['selected_session_id'],**kwargs)

def test_complete_sqlite_inventory_counts_schema_metadata_and_unknown_without_mutation():
    before={p.name:p.read_bytes() for p in (PACKAGE/'native-bundle').iterdir()}
    inventory=build(complete_record_family=True)
    rows=inventory.evidence['records']
    assert inventory.evidence['evidence_complete']
    assert len({r['record_id'] for r in rows})==len(rows)
    assert any(r['record_id'].startswith('sqlite_master:') for r in rows)
    assert any(r['classification']=='unknown' for r in rows)
    assert any(r['classification']=='useful' for r in rows)
    assert all(r['logical_bytes']>0 for r in rows)
    assert before=={p.name:p.read_bytes() for p in (PACKAGE/'native-bundle').iterdir()}

@pytest.mark.parametrize('member',['opencode.db','opencode.db-wal','opencode.db-shm'])
def test_every_physical_member_is_hash_bound(member):
    m=manifest();next(r for r in m['files'] if r['name']==member)['sha256']='0'*64
    with pytest.raises(ValueError,match='physical hash'):build(m,complete_record_family=True)

def test_missing_family_proof_keeps_density_unresolved():
    assert build().evidence['evidence_complete'] is False

def test_second_session_claim_rejected():
    m=manifest();m['selected_session_id']='unrelated'
    with pytest.raises(ValueError):build(m,complete_record_family=True)

def test_optional_profile_density_binds_db_and_both_companions():
    doc=build_opencode_format_evidence(PACKAGE,collected_on='2026-09-29',complete_record_family=True,include_native_density=True)
    metrics={r['id']:r for r in validate_format_evidence(doc)['profile']['metrics']}
    assert metrics['broad.classified_content_density']['state']=='measured'
    row=next(r for r in doc['metric_evidence'] if r['metric_id']=='broad.classified_content_density')
    assert len(row['native_locators'])==3


@pytest.mark.parametrize('payload',[
    '{"type":"opaque-new-type","type":"text","text":"fabricated"}',
    '{"type":"text","text":"fabricated","extra":NaN}',
])
def test_malformed_embedded_json_never_receives_useful_credit(tmp_path,payload):
    import hashlib
    import sqlite3
    con=sqlite3.connect(tmp_path/'opencode.db')
    try:
        con.execute('PRAGMA journal_mode=WAL')
        con.executescript('CREATE TABLE session(id TEXT); CREATE TABLE message(id TEXT,data TEXT); CREATE TABLE part(message_id TEXT,data TEXT);')
        con.execute('INSERT INTO session VALUES (?)',('s',))
        con.execute('INSERT INTO message VALUES (?,?)',('m','{"role":"assistant"}'))
        con.execute('INSERT INTO part VALUES (?,?)',('m',payload));con.commit()
        files=[{'name':p.name,'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'size_bytes':p.stat().st_size} for p in tmp_path.iterdir()]
        with pytest.raises(ValueError):
            build_opencode_density(tmp_path,manifest={'files':files},session_id='s',complete_record_family=True)
    finally:con.close()


def test_event_table_is_in_the_read_and_its_repeats_are_snapshots():
    live=ROOT/'artifacts/v1-expanded-preparation/opencode-1.18.31-live-v1/opencode-1-18-31-eval-1'
    import hashlib
    files=[{'name':p.name,'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'size_bytes':p.stat().st_size} for p in sorted((live/'native-bundle').iterdir())]
    session=json.loads((live/'observer.json').read_text())['events'][0]['session_id']
    rows=build_opencode_density(live/'native-bundle',manifest={'files':files},session_id=session,complete_record_family=True).evidence['records']
    kinds=lambda table:{r['record_kind'] for r in rows if r['record_id'].startswith(table+':')}
    # A part event holds a whole part, so it restates what the part row states (a snapshot). An event that states nothing is metadata.
    # The part rows come first in the read, so no event row is the first record of an event: none is useful.
    assert kinds('event')=={'snapshot','metadata'} and len([r for r in rows if r['record_id'].startswith('event:')])==149
    assert kinds('migration')=={'metadata'} and kinds('session')=={'metadata'} and kinds('message')=={'metadata'}
    assert kinds('part')=={'user_message','assistant_message','tool_result','metadata','unknown'}


def test_forward_occurrences_count_every_statement_of_the_read_including_the_event_table():
    from collections import Counter
    from session_bench.opencode_density import opencode_forward_occurrences
    live=ROOT/'artifacts/v1-expanded-preparation/opencode-1.18.31-live-v1/opencode-1-18-31-eval-1'
    session=json.loads((live/'observer.json').read_text())['events'][0]['session_id']
    occurrences=opencode_forward_occurrences(live/'native-bundle',session_id=session)
    events=Counter(event for event,_ in occurrences)
    assert (len(occurrences),len(events))==(79,29)
    assert sorted({event.split(':')[0] for event in events})==['call','message','result']
    assert min(events.values())==2
    # A running state that holds the full output states the result (3 per run); a pending state does not state the call.
    assert sum(1 for _,occurrence in occurrences if occurrence.startswith('event:'))==50


def test_running_state_with_the_full_output_restates_the_result_whatever_its_status():
    from session_bench.opencode_density import part_statements
    def row(status,output):
        state={'status':status,'input':{'command':'ls'},'metadata':{'output':output}}
        return {'id':'prt_1','data':json.dumps({'type':'tool','callID':'call_1','state':state})}
    finals={'call_1':'a\nb'}
    assert part_statements(row('running','a\nb'),'assistant',finals)[0]==['call:call_1','result:call_1']
    # A partial output is not the full result.
    assert part_statements(row('running','a'),'assistant',finals)[0]==['call:call_1']
    assert part_statements(row('running',''),'assistant',finals)[0]==['call:call_1']
    assert part_statements(row('running','a\nb'),'assistant')[0]==['call:call_1']
