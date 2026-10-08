import hashlib
import importlib.util
from pathlib import Path

spec=importlib.util.spec_from_file_location('claude_sanitize',Path(__file__).resolve().parents[1]/'scripts/sanitize_claude_score_packets.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


def test_same_size_aliases_and_transitive_hash_rebinding():
    native=b'{"email":"syntheticperson@gmail.com","cwd":"/Users/person/synthetic"}'
    manifest=b'{"sha256":"'+hashlib.sha256(native).hexdigest().encode()+b'"}'
    wrapper=b'{"sha256":"'+hashlib.sha256(manifest).hexdigest().encode()+b'"}'
    originals={'native/session':native,'native/decode':manifest,'inputs/proof':wrapper}
    changed, counts=module.transform_documents(originals)
    assert counts=={'email_alias_count':1,'home_alias_count':1}
    assert all(len(changed[key])==len(value) for key,value in originals.items())
    assert b'gmail.com' not in changed['native/session']
    assert hashlib.sha256(changed['native/session']).hexdigest().encode() in changed['native/decode']
    assert hashlib.sha256(changed['native/decode']).hexdigest().encode() in changed['inputs/proof']
    assert b'person' not in changed['native/session']


def test_vendor_attribution_is_unchanged():
    values={'native/session':b'Co-Authored-By: Claude <noreply@anthropic.com>'}
    assert module.transform_documents(values)[0]==values


def test_public_receipt_carries_no_hash_of_a_private_original():
    # With a short alias, a published hash of the original confirms a guessed name.
    import json
    original={'inputs/a.json':b'{"cwd":"/Users/alice/x"}','native/s.jsonl':b'{"p":"/Users/alice"}'}
    transformed={'inputs/a.json':b'{"cwd":"/Users/xxxxx/x"}','native/s.jsonl':b'{"p":"/Users/xxxxx"}'}
    public,private=module.transformation_receipts(original,transformed,parent_manifest_sha256='f'*64,description='d',aliases={'home_alias_count':1})
    text=json.dumps(public)
    assert 'f'*64 not in text
    assert all(hashlib.sha256(data).hexdigest() not in text for data in original.values())
    assert [row['transformed_sha256'] for row in public['files']]==[hashlib.sha256(transformed[name]).hexdigest() for name in sorted(transformed)]
    assert private['parent_manifest_sha256']=='f'*64
    assert [row['original_sha256'] for row in private['files']]==[hashlib.sha256(original[name]).hexdigest() for name in sorted(original)]


def test_no_public_packet_holds_the_hash_of_a_changed_private_file_of_another_packet(tmp_path):
    # Each packet carries the root evidence of the other two runs. That evidence names the
    # transcript hash of those runs. A hash of a private transcript confirms a guessed e-mail.
    import re
    source=Path(__file__).resolve().parents[1]/'artifacts/v1-expanded-preparation/claude-cli-root-score-replay-v6'
    output=tmp_path/'public'
    module.build(source,output)
    runs=[f'claude-cli-eval-{number}' for number in (1,2,3)]
    public=b'\n'.join(path.read_bytes() for run in runs for path in sorted((output/run).rglob('*')) if path.is_file())
    published=set(re.findall(rb'[0-9a-f]{64}',public))
    changed=0
    for run in runs:
        for folder in ('native','inputs'):
            for path in sorted((source/run/folder).rglob('*')):
                if not path.is_file(): continue
                data=path.read_bytes()
                if data!=(output/run/path.relative_to(source/run)).read_bytes():
                    changed+=1
                    assert hashlib.sha256(data).hexdigest().encode() not in published, path.relative_to(source).as_posix()
    assert changed>=3


def test_attempt_digests_of_files_outside_the_packet_are_zeroed_at_equal_length():
    import json
    attempt={'evidence_31':{'sha256':'a'*64,'path':'evidence-31.json'},'broad_wrapper':{'sha256':'b'*64},'helper_ledger':{'sha256':'c'*64},
             'turn1':{'stdout_sha256':'d'*64,'exit_code':0},'turn2':{'stdout_sha256':'e'*64},'decoder':{'source_sha256':'f'*64},'protected_bench_check_sha256':'1'*64}
    data=json.dumps(attempt).encode()
    changed=module.zero_unbound_attempt_digests('claude-cli-eval-1/inputs/attempt.json',data)
    value=json.loads(changed)
    assert len(changed)==len(data) and value['protected_bench_check_sha256']=='1'*64 and value['evidence_31']['path']=='evidence-31.json'
    assert all(value[section][field]=='0'*64 for section,field in module.UNBOUND_ATTEMPT_DIGESTS)
    assert module.zero_unbound_attempt_digests('claude-cli-eval-1/inputs/observer.json',data)==data


def test_vendor_attachment_text_is_blanked_like_the_desktop_row_and_scored_records_stay():
    import json
    rows=[
        {'type':'queue-operation','operation':'enqueue','sessionId':'s','content':'Requirement R1: fix checkout. '*30},
        {'type':'user','uuid':'u','sessionId':'s','message':{'role':'user','content':'Requirement R1: fix checkout. '*30}},
        {'type':'attachment','uuid':'p','sessionId':'s','attachment':{'type':'prompt_snapshot','systemPrompt':['You are an interactive CLI tool. '*40],
            'tools':[{'name':'Bash','description':'Runs a command. '*50,'schema':{'description':'Runs a command. '*50,'input_schema':{'properties':{'command':{'type':'string','description':'The command.'}}}}}]}},
        {'type':'attachment','uuid':'o','sessionId':'s','attachment':{'type':'output_style','style':'Concise','turnReminder':'Be concise.'},'rendered':[{'role':'user','content':'<system-reminder>Be concise.</system-reminder>'}]},
        {'type':'attachment','uuid':'m','sessionId':'s','attachment':{'type':'model','identity':{'modelId':'claude-sonnet-5[1m]'}}},
        {'type':'assistant','uuid':'a','sessionId':'s','message':{'role':'assistant','model':'claude-sonnet-5','content':[{'type':'tool_use','id':'t','name':'Bash','input':{'command':'ls','description':'list'}}]}},
        {'type':'user','uuid':'r','sessionId':'s','message':{'role':'user','content':[{'type':'tool_result','tool_use_id':'t','content':'a.txt'}]},'toolUseResult':{'stdout':'a.txt'}},
        {'type':'last-prompt','sessionId':'s','lastPrompt':'Requirement R1'},
        {'type':'attachment','uuid':'e','sessionId':'s','attachment':{'type':'environment','snapshot':{'workingDirectory':'/private/tmp/run','platform':'darwin','osVersion':'Darwin 24.6.0'}}},
    ]
    data=b'\n'.join(json.dumps(row).encode() for row in rows)+b'\n'
    changed,count=module.redact_claude_transcript(data)
    after=[json.loads(line) for line in changed.splitlines()]
    assert len(changed)==len(data) and count>0
    marker='[vendor instruction text removed at equal byte length]'
    snapshot=after[2]['attachment']
    assert snapshot['type']=='prompt_snapshot' and snapshot['systemPrompt'][0].startswith(marker)
    tool=snapshot['tools'][0]
    # Tool descriptions and schema descriptions go; every key stays.
    assert tool['description'].startswith(marker) and tool['schema']['description'].startswith(marker)
    assert list(tool['schema']['input_schema']['properties'])==['command'] and tool['schema']['input_schema']['properties']['command']['description']=='[vendor inst'
    assert after[3]['attachment']['type']=='output_style' and after[3]['rendered'][0]['content']==marker[:len(rows[3]['rendered'][0]['content'])]
    assert after[4]['attachment']=={'type':'model','identity':{'modelId':marker[:len('claude-sonnet-5[1m]')]}}
    # One value stays that the Desktop rule blanks: the platform, which states the operating system of the capture.
    assert after[8]['attachment']['snapshot']=={'workingDirectory':marker[:len('/private/tmp/run')],'platform':'darwin','osVersion':marker[:len('Darwin 24.6.0')]}
    # Records that a metric reads are untouched.
    for index in (0,1,5,6,7): assert after[index]==rows[index]
    # The same strings are blanked as by the Desktop rule: both leave the same strings unchanged.
    from sanitize_claude_desktop_score_packets import redact_desktop_transcript
    desktop=[json.loads(line) for line in redact_desktop_transcript(data)[0].splitlines()]
    unchanged=lambda result:[left==right for left,right in zip(_strings(result),_strings(rows))]
    differ=[index for index,(left,right) in enumerate(zip(unchanged(after),unchanged(desktop))) if left!=right]
    assert len(differ)==1 and list(_strings(rows))[differ[0]]=='darwin' and not all(unchanged(after))


def _strings(value):
    if isinstance(value,dict):
        for item in value.values(): yield from _strings(item)
    elif isinstance(value,list):
        for item in value: yield from _strings(item)
    elif isinstance(value,str): yield value
