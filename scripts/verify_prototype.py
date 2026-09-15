#!/usr/bin/env python3
"""Verify copied pilot evidence offline on macOS; never contacts a vendor."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from session_bench.prototype_metrics import analyze_capture
from session_bench.isolation import macos_profile


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify(captures):
    captures = Path(captures).resolve()
    directories = sorted(p.parent for p in captures.glob('*/metadata.json'))
    if not directories:
        raise ValueError('No captures')
    before = {str(p): digest(p) for d in directories for p in (d/'native').iterdir()}
    results = []
    with tempfile.TemporaryDirectory(prefix='session-bench-verify-') as temporary:
        temp = Path(temporary).resolve()
        code = temp/'code'/'session_bench'
        code.mkdir(parents=True)
        (code/'__init__.py').write_text('')
        shutil.copyfile(ROOT/'session_bench/prototype_metrics.py', code/'prototype_metrics.py')
        worker = '''import json,sys,socket
from pathlib import Path
from session_bench.prototype_metrics import analyze_capture
try:
    Path(sys.argv[2]).read_bytes()
except PermissionError:
    pass
else:
    raise AssertionError('original evidence remained readable')
try:
    socket.socket().bind(('127.0.0.1', 0))
except PermissionError:
    pass
else:
    raise AssertionError('network remained available')
result=analyze_capture(Path(sys.argv[1]))
result.pop('evidence_path',None)
print(json.dumps(result,sort_keys=True))
'''
        for directory in directories:
            copied = temp/directory.name
            shutil.copytree(directory, copied)
            expected = analyze_capture(directory)
            expected.pop('evidence_path', None)
            command = ['/usr/bin/sandbox-exec', '-p', macos_profile([temp], [temp]),
                       sys.executable, '-I', '-S', '-B', '-c',
                       'import sys;sys.path.insert(0,'+repr(str(code.parent))+');'+worker,
                       str(copied), str(directory/'metadata.json')]
            process = subprocess.run(command, cwd=temp, capture_output=True, text=True,
                                     timeout=45, env={'PATH':'/usr/bin:/bin','HOME':str(temp),'TMPDIR':str(temp)})
            if process.returncode:
                raise RuntimeError(process.stderr)
            actual = json.loads(process.stdout)
            assert actual == expected, directory.name
            results.append({'capture':directory.name, 'copied_result_identical':True,
                            'original_read_denied':True, 'network_denied':True})

        damaged = temp/directories[0].name
        observer = json.loads((damaged/'observer.json').read_text())
        failure = next(e for e in observer['events'] if e['kind']=='failure')
        marker = failure['text'].split()[0]
        meta = json.loads((damaged/'metadata.json').read_text())
        changed = 0
        for entry in meta['native_files']:
            path = damaged/entry['path']
            raw = path.read_bytes()
            changed += raw.count(marker.encode())
            raw = raw.replace(marker.encode(), b'CONTROL_REMOVED_FAILURE_MARKER')
            path.write_bytes(raw)
            entry['sha256'] = digest(path)
            entry['bytes'] = len(raw)
        assert changed > 0
        manifest = json.dumps(sorted(meta['native_files'], key=lambda x:x['path']),
                              sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()
        observer['artifact_manifest_sha256'] = hashlib.sha256(manifest).hexdigest()
        (damaged/'metadata.json').write_text(json.dumps(meta))
        (damaged/'observer.json').write_text(json.dumps(observer))
        control = analyze_capture(damaged)
        check = next(m for m in control['measurements'] if m['id']=='W3')
        assert check['score'] is None and check['state']=='unresolved', check
    assert before == {p:digest(Path(p)) for p in before}, 'Original evidence changed'
    return {'scope':'Local copied-bundle reproduction; not independent human reproduction',
            'captures':results, 'original_native_hashes_unchanged':True,
            'negative_control':{'removed_marker_occurrences':changed,'W3':check},
            'native_sha256':before}


if __name__ == '__main__':
    result = verify(ROOT/'artifacts/prototype-v1/captures')
    output = ROOT/'artifacts/prototype-v1/verification.json'
    output.write_text(json.dumps(result,indent=2)+'\n')
    print(output)
