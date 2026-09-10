"""OS-enforced decoder input isolation for the bounded offline prototype.

macOS uses sandbox-exec. Other systems fail closed until a tested isolation
backend is supplied; library decode_native remains usable for fixture tests.
"""
import json
import resource
from pathlib import Path
import subprocess
import sys
import tempfile

from .bundle import canonical, safe_json, safe_read, safe_path


def _quote(path):
    return json.dumps(str(Path(path).resolve()))


def macos_profile(read_roots, write_roots=()):
    rules = ['(version 1)', '(deny default)', '(allow process-exec)', '(deny process-fork)', '(allow sysctl-read)',
             '(allow mach-lookup)', '(allow file-read-metadata)', '(allow file-read* (literal "/"))']
    system = ['/System', '/usr', '/bin', '/sbin', '/Library', '/opt/homebrew', '/private/etc', '/dev']
    for root in [*system, *read_roots, *write_roots]:
        rules.append(f'(allow file-read* (subpath {_quote(root)}))')
    for root in write_roots:
        rules.append(f'(allow file-write* (subpath {_quote(root)}))')
    # Network operations have no allow rule. HOME and arbitrary /Users roots have none.
    return '\n'.join(rules)


def isolated_decode(package, *, code_root=None):
    if sys.platform != 'darwin' or not Path('/usr/bin/sandbox-exec').exists():
        raise ValueError('OS-enforced decoding currently requires macOS sandbox-exec; no unsandboxed fallback')
    package = Path(package).resolve()
    code = Path(code_root).resolve() if code_root else Path(__file__).resolve().parent
    with tempfile.TemporaryDirectory(prefix='sb-decode-') as directory:
        temp = Path(directory).resolve()
        # Snapshot through no-follow handles before giving a decoder any input.
        # The private snapshot cannot expose observer/expected sibling files.
        index=safe_json(package,'decode.json')
        if not isinstance(index,dict) or set(index)!={'format','artifacts'} or not isinstance(index['artifacts'],list) or len(index['artifacts'])>256:
            raise ValueError('invalid native-only package index')
        snapshot=temp/'native';snapshot.mkdir()
        (snapshot/'decode.json').write_bytes(canonical(index))
        for entry in index['artifacts']:
            if not isinstance(entry,dict) or not isinstance(entry.get('path'),str):
                raise ValueError('invalid native artifact entry')
            target=safe_path(snapshot,entry['path'])
            if entry['path']=='decode.json':
                raise ValueError('native index cannot be an artifact')
            target.parent.mkdir(parents=True,exist_ok=True)
            try:
                contents=safe_read(package,entry['path'])
            except FileNotFoundError:
                continue  # decoder diagnoses declared-but-missing artifacts
            target.write_bytes(contents)
        # Validate undeclared files before snapshotting would otherwise hide them.
        from .decoders import _validate_package
        _validate_package(package)
        decoder_code=temp/'code'/'session_bench';decoder_code.mkdir(parents=True)
        # Do not grant the worker access to fixtures.py, evaluator, or schemas:
        # fixture source itself contains constructed answers. Stage only decoder code.
        for filename in ('__init__.py','decoders.py'):
            (decoder_code/filename).write_bytes(safe_read(code,filename))
        profile = macos_profile([decoder_code, snapshot], [temp])
        # Worker receives only native package, not the evaluation manifest/answer key.
        worker = "import json,sys; from pathlib import Path; from session_bench.decoders import decode_native; print(json.dumps(decode_native(Path(sys.argv[1])),sort_keys=True,ensure_ascii=False,allow_nan=False))"
        command = ['/usr/bin/sandbox-exec', '-p', profile, sys.executable, '-I', '-S', '-B', '-c',
                   'import sys; sys.path.insert(0,' + repr(str(decoder_code.parent)) + ');' + worker, str(snapshot)]
        env = {'PATH':'/usr/bin:/bin', 'HOME':str(temp), 'TMPDIR':str(temp), 'PYTHONDONTWRITEBYTECODE':'1', 'PYTHONHASHSEED':'0'}
        def limits():
            resource.setrlimit(resource.RLIMIT_CPU, (20, 20))
            resource.setrlimit(resource.RLIMIT_FSIZE, (64*1024*1024, 64*1024*1024))
            resource.setrlimit(resource.RLIMIT_NOFILE, (128,128))
        result = subprocess.run(command, cwd=temp, env=env, text=True, capture_output=True, timeout=60, preexec_fn=limits)
        if result.returncode:
            raise ValueError(f'isolated decoder failed ({result.returncode}): ' + result.stderr.strip())
        return json.loads(result.stdout)
