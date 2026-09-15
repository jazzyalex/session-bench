#!/usr/bin/env python3
"""Generate the local pilot report from captured evidence. Never launches models."""
import argparse
import json
from pathlib import Path
import shutil
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from session_bench.prototype_metrics import build_report
from session_bench.prototype_report import render_report

TARGETS=[('codex-cli','Codex CLI','CLI / exec noninteractive'),
         ('codex-desktop','Codex Desktop','Desktop / native task API; UI unobserved'),
         ('opencode-cli','OpenCode CLI','CLI / run noninteractive')]

def build(captures,out):
    captures=Path(captures).resolve(); out=Path(out).resolve()
    if out==captures or out.is_relative_to(captures): raise ValueError('output must be outside captures')
    if out.exists() and any(out.iterdir()): raise ValueError('output must be empty; preserve earlier report')
    directories=sorted(p.parent for p in captures.glob('*/metadata.json'))
    report=build_report(directories)
    report['edition']='v1 prototype · live pilots'
    report['headline']='Your agent wrote the code. What did it record?'
    report['method']='One small checkout task: inspect, expose a failing test, correct the delivery requirement, edit, and rerun. These are live pilots, not a frozen repeated-run ranking.'
    failures=[next((m for m in row.get('measurements',[]) if m['id']=='W3'),{}) for row in report['configurations']]
    if len(failures)==3 and all(m.get('score')==100 for m in failures):
        count=sum(m['denominator'] for m in failures)
        report['deck']=f'All three pilots kept the failures: {count} of {count} independently observed failing test results were found in their saved records. The rest of the record is a more mixed picture.'
    out.mkdir(parents=True,exist_ok=True)
    index={}
    for p in directories:
        meta=json.loads((p/'metadata.json').read_text())
        index.setdefault(meta['id'],[]).append(p)
        shutil.copytree(p,out/'evidence'/p.name)
    for row in report['configurations']:
        ds=index.get(row['id'],[])
        if ds:
            row['evidence_path']='evidence/'+ds[0].name+'/metadata.json'
            for p in ds:
                row.setdefault('limitations',[]).extend(json.loads((p/'metadata.json').read_text()).get('limitations',[]))
    for identity,name,surface in TARGETS:
        if not any(r['id']==identity for r in report['configurations']):
            report['configurations'].append({'id':identity,'name':name,'surface':surface,'version':'unverified','model':'unverified','status':'unavailable','score':None,'coverage':0,'runs':0,'categories':[{'id':c['id'],'name':c['name'],'weight':c['weight'],'score':None,'summary':'No qualifying capture yet'} for c in report['categories']],'facts':[],'composition':[],'native_bytes':None,'examples':[],'limitations':['No qualifying native capture available in this report snapshot.'],'evidence_path':''})
    report.setdefault('limitations',[]).extend([
        'Prototype edition: category measurements describe these captured runs. No public overall ranking or general coding-quality claim.',
        'CLI runs are noninteractive command modes. Desktop uses the supported native task API; manual GUI behavior and displayed content were not independently observed.',
        'Runtime/configuration and setup differences make footprint values descriptive, not a controlled product-efficiency ranking.',
        'This local evidence package has not been approved or sanitized for public distribution.'
    ])
    render_report(report,out)
    print(out/'index.html')
    return report

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--captures',type=Path,default=Path('artifacts/prototype-v1/captures'));parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args();build(args.captures,args.out)
