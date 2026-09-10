"""OS-enforced tests; unsupported platforms skip rather than claim isolation."""
import errno
import json
from pathlib import Path
import subprocess
import sys

import pytest

from session_bench.bundle import canonical
from session_bench.fixtures import build_fixture
from session_bench.isolation import isolated_decode, macos_profile, _quote
from session_bench.reproduce import reproduce

pytestmark=pytest.mark.skipif(sys.platform!='darwin',reason='tested OS isolation backend is macOS sandbox-exec')


def test_decoder_cannot_read_answer_key_or_network(tmp_path):
    bundle=build_fixture(tmp_path/'input')
    scratch=tmp_path/'scratch';scratch.mkdir()
    native=bundle/'native'
    profile=macos_profile([native],[scratch])
    probe="""
import errno,sys,socket
from pathlib import Path
assert Path(sys.argv[1]).read_bytes()
try:
    Path(sys.argv[2]).read_bytes()
except PermissionError:
    pass
else:
    raise RuntimeError('answer key readable')
s=socket.socket()
try:
    assert s.connect_ex(('127.0.0.1',9)) in (errno.EPERM,errno.EACCES)
finally:
    s.close()
print('native readable; answer key and network denied')
"""
    result=subprocess.run(['/usr/bin/sandbox-exec','-p',profile,sys.executable,'-I','-S','-c',probe,str(native/'decode.json'),str(bundle/'expected/assertions.json')],cwd=scratch,capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    before=isolated_decode(native)
    (bundle/'expected/assertions.json').write_text('{"poison":"observer-only-marker"}')
    after=isolated_decode(native)
    assert canonical(before)==canonical(after)
    assert b'observer-only-marker' not in canonical(after)


def test_copied_bundle_reproduction_with_originals_and_network_denied(tmp_path):
    receipt=reproduce(tmp_path/'reproduction')
    assert len(receipt['cases'])==8
    for case in receipt['cases']:
        assert case['identical_semantic_output']
        assert case['denial_probes']=={'network':'denied','original_bundle':'denied','original_code':'denied','site_loaded':False}
    positive=[c for c in receipt['cases'] if c['name'].endswith('intact')]
    loss=[c for c in receipt['cases'] if c['name'].endswith('remove_fact')]
    assert all(c['assertions']['pass']>0 and c['assertions']['fail']==0 for c in positive)
    assert all(c['assertions']['fail']>0 for c in loss)
