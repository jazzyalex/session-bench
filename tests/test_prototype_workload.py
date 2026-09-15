import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
def module(name):
    spec=importlib.util.spec_from_file_location(name,ROOT/'scripts'/f'{name}.py')
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

def test_helper_records_actual_failure_and_success(tmp_path):
    workload=module('prototype_workload');workload.prepare(tmp_path)
    def run(phase):
        return subprocess.run([sys.executable,'bench_check.py',phase],cwd=tmp_path,capture_output=True,text=True)
    failed=run('baseline');assert failed.returncode==1
    first=json.loads((tmp_path/'.bench-observer.jsonl').read_text().splitlines()[0])
    assert first['exit_code']==1 and first['result']==failed.stdout.rstrip('\n')
    (tmp_path/'checkout.py').write_text('def checkout(items):\n    subtotal=sum(p*q for p,q in items)\n    return subtotal+(0 if subtotal>=50 else 5)\n')
    success=run('final');assert success.returncode==0
    second=json.loads((tmp_path/'.bench-observer.jsonl').read_text().splitlines()[1])
    assert second['exit_code']==0 and second['result']==success.stdout.rstrip('\n')
    assert first['id']!=second['id']

def test_package_rejects_modified_observer_helper(tmp_path):
    import pytest
    workspace=tmp_path/'workspace';capture=tmp_path/'capture';(capture/'native').mkdir(parents=True)
    module('prototype_workload').prepare(workspace)
    (workspace/'bench_check.py').write_text('print("fabricated")')
    with pytest.raises(ValueError,match='helper changed'):
        module('prototype_package').package(capture,workspace,{},'codex-rollout-jsonl',[])
