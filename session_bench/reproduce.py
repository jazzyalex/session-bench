"""Copied-source, copied-evidence offline reproduction with OS denial probes."""
import errno
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from .bundle import canonical, digest
from .evaluate import evaluate_bundle, implementation_digest
from .fixtures import build_fixture
from .isolation import macos_profile, _quote, isolated_decode


def reproduce(out):
    if sys.platform != 'darwin':
        raise ValueError('this reproduction backend requires macOS sandbox-exec')
    out=Path(out).resolve()
    if out.exists() and any(out.iterdir()):
        raise ValueError('reproduction output must be empty')
    out.mkdir(parents=True,exist_ok=True)
    repo=Path(__file__).resolve().parent.parent
    receipt={'schema_version':'1.0-prototype','implementation_sha256':implementation_digest(),
             'scope':'local independent process recomputation, not independent person or vendor evidence',
             'runtime':{'python':sys.version.split()[0],'platform':sys.platform,'dependencies':'Python standard library only'},
             'isolation':'macOS sandbox-exec; Python -I -S; network and original sources denied', 'cases':[]}
    with tempfile.TemporaryDirectory(prefix='sb-reproduction-') as temporary:
        workspace=Path(temporary).resolve()
        kit=workspace/'kit';kit.mkdir()
        shutil.copytree(repo/'session_bench',kit/'session_bench',ignore=shutil.ignore_patterns('__pycache__'))
        shutil.copytree(repo/'schemas',kit/'schemas')
        for fmt in ['constructed-jsonl-v1','constructed-sqlite-v1']:
            for mutation in [None,'remove_fact','wrong_status','captured_corruption','attachment_payload']:
                name=fmt.removeprefix('constructed-')+'-'+(mutation or 'intact')
                original=workspace/'originals'/name
                build_fixture(original,fmt,mutation)
                baseline,_=evaluate_bundle(original)
                copied=workspace/'copies'/name
                shutil.copytree(original,copied)
                target=out/name
                target.mkdir()
                native_decoded=isolated_decode(copied/'native',code_root=kit/'session_bench')
                decoder_output=target/'native-decoded.json'
                decoder_output.write_bytes(canonical(native_decoded)+b'\n')
                temp=workspace/'scratch'/name;temp.mkdir(parents=True)
                # Original fixture AND original repository cannot be read here.
                profile=macos_profile([kit,copied],[target,temp])
                profile += f'\n(allow file-read* (literal {_quote(kit)}))'
                probe = """
import errno,json,socket,sys
from pathlib import Path
proof={}
for label,path in [('original_bundle',sys.argv[1]),('original_code',sys.argv[2])]:
    try:
        Path(path).read_bytes()
    except PermissionError:
        proof[label]='denied'
    else:
        raise RuntimeError(label+' unexpectedly accessible')
s=socket.socket()
try:
    code=s.connect_ex(('127.0.0.1',9))
finally:
    s.close()
if code not in (errno.EPERM,errno.EACCES):
    raise RuntimeError('network denial was not enforced: '+str(code))
proof['network']='denied'
proof['site_loaded']='site' in sys.modules
print(json.dumps(proof,sort_keys=True))
"""
                env={'PATH':'/usr/bin:/bin','HOME':str(temp),'TMPDIR':str(temp),'PYTHONDONTWRITEBYTECODE':'1','PYTHONHASHSEED':'0'}
                prefix=['/usr/bin/sandbox-exec','-p',profile,sys.executable,'-I','-S','-B']
                checks=subprocess.run(prefix+['-c',probe,str(original/'manifest.json'),str(repo/'session_bench/evaluate.py')],cwd=kit,env=env,capture_output=True,text=True,timeout=30)
                if checks.returncode:
                    raise ValueError('isolation probe failed: '+checks.stderr)
                # Two separately sandboxed stages avoid macOS nested-seatbelt
                # reinitialization. The parent passes ONLY native decoder output.
                worker="""
import sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from session_bench.bundle import canonical,read_json
from session_bench.evaluate import evaluate_bundle
from session_bench.__main__ import render
result,decoded=evaluate_bundle(Path(sys.argv[2]),decoder=lambda _:read_json(sys.argv[3]))
out=Path(sys.argv[4])
(out/'results.json').write_bytes(canonical(result)+b'\\n')
(out/'decoded.json').write_bytes(canonical(decoded)+b'\\n')
(out/'report.md').write_text(render(result))
"""
                executed=subprocess.run(prefix+['-c',worker,str(kit),str(copied),str(decoder_output),str(target)],cwd=kit,env=env,capture_output=True,text=True,timeout=90)
                if executed.returncode:
                    raise ValueError('copied offline evaluation failed: '+executed.stderr)
                reproduced=json.loads((target/'results.json').read_text())
                same=canonical(baseline)==canonical(reproduced)
                if not same:
                    raise ValueError('semantic recomputation mismatch: '+name)
                receipt['cases'].append({'name':name,'origin':baseline['origin'],
                    'manifest_sha256':baseline['manifest_sha256'],'evaluation_id':baseline['evaluation_id'],
                    'semantic_sha256':digest(canonical(baseline)),'identical_semantic_output':same,
                    'denial_probes':json.loads(checks.stdout),
                    'assertions':{state:sum(r['state']==state for r in baseline['rows']) for state in ('pass','fail','unresolved')}})
    (out/'reproduction-receipt.json').write_bytes(canonical(receipt)+b'\n')
    return receipt


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',required=True,type=Path)
    args=parser.parse_args()
    print(json.dumps(reproduce(args.out),indent=2))
