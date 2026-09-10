"""Closed, integrity-checked evidence packages. No acquisition or home discovery."""
import hashlib
import json
import os
import stat
from pathlib import Path, PurePosixPath
from .schema import validate_named


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def _object(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError(f"duplicate JSON key: {key}")
        obj[key] = value
    return obj


def read_json(path):
    def invalid(value):
        raise ValueError(f"nonfinite JSON number: {value}")
    return json.loads(Path(path).read_text(encoding="utf-8"), object_pairs_hook=_object, parse_constant=invalid)



def safe_read(root, relative, limit=16*1024*1024):
    """Read through no-follow directory handles, preventing path-swap escapes."""
    safe_path(root, relative)
    descriptor=os.open(root, os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        parts=relative.split('/')
        for component in parts[:-1]:
            child=os.open(component, os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor=child
        fd=os.open(parts[-1],os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=descriptor)
        try:
            info=os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_size>limit:
                raise ValueError('evidence must be a regular file within 16 MiB prototype limit')
            with os.fdopen(fd,'rb',closefd=False) as stream:
                data=stream.read(limit+1)
            if len(data)>limit:
                raise ValueError('evidence exceeds prototype byte limit')
            return data
        finally:
            os.close(fd)
    finally:
        os.close(descriptor)


def safe_json(root, relative):
    def invalid(value):
        raise ValueError(f'nonfinite JSON number: {value}')
    return json.loads(safe_read(root,relative),object_pairs_hook=_object,parse_constant=invalid)

def safe_path(root, relative):
    if not isinstance(relative, str) or not relative or "\\" in relative:
        raise ValueError("invalid relative path")
    p = PurePosixPath(relative)
    if p.is_absolute() or any(x in ("", ".", "..") for x in relative.split("/")):
        raise ValueError(f"path escape/noncanonical path: {relative}")
    root = Path(root)
    candidate = root
    for part in p.parts:
        candidate = candidate / part
        if candidate.is_symlink():
            raise ValueError(f"symlink not allowed: {relative}")
    if not candidate.resolve().is_relative_to(root.resolve()):
        raise ValueError(f"path escape: {relative}")
    return candidate


def unique(items, key, context):
    values = [x[key] for x in items]
    if len(set(values)) != len(values):
        raise ValueError(f"duplicate {key} in {context}")


def dependencies(artifacts):
    by_id = {a['id']: a for a in artifacts}
    visited, active = set(), set()
    def visit(aid):
        if aid not in by_id:
            raise ValueError(f"dangling dependency: {aid}")
        if aid in active:
            raise ValueError(f"cyclic artifact dependency: {aid}")
        if aid in visited:
            return
        active.add(aid)
        for dep in by_id[aid]['depends_on']:
            visit(dep)
        active.remove(aid)
        visited.add(aid)
    for aid in by_id:
        visit(aid)


def validate_registry(registry):
    validate_named(registry, 'registry')
    unique(registry['surfaces'], 'id', 'registry')
    for row in registry['surfaces']:
        if row['status'] == 'measured' and row['live_tested_at'] is None:
            raise ValueError('measured surface requires live_tested_at')
    return registry


def validate_result(result):
    validate_named(result, 'result')
    unique(result['rows'], 'id', 'result')
    ids = {r['id'] for r in result['rows']}
    by_id = {r['id']: r for r in result['rows']}
    for m in result['metrics']:
        if len(set(m['assertion_ids'])) != len(m['assertion_ids']):
            raise ValueError('duplicate metric assertion IDs')
        if not set(m['assertion_ids']) <= ids:
            raise ValueError('metric references unknown assertion')
        selected = [by_id[x] for x in m['assertion_ids']]
        if m['unit'] != 'assertions':
            raise ValueError('prototype aggregate metrics require assertion units; fields remain per-row')
        total, passed = len(selected), sum(r['state']=='pass' for r in selected)
        state = 'unresolved' if not total or any(r['state']=='unresolved' for r in selected) else 'pass' if passed==total else 'fail'
        if (m['denominator'],m['numerator'],m['state']) != (total,passed,state):
            raise ValueError('metric population/count/state mismatch (zero denominator cannot pass)')
        if m['scope'] != 'all' and any(r['scenario']!=m['scope'] for r in selected):
            raise ValueError('metric scope differs from population')
    return result


def validate_bundle(root):
    root = Path(root)
    if root.is_symlink() or not root.is_dir():
        raise ValueError('missing evidence bundle or symlink root')
    manifest_path = safe_path(root, 'manifest.json')
    if not manifest_path.is_file():
        raise ValueError('missing manifest')
    manifest = safe_json(root, 'manifest.json')
    validate_named(manifest, 'manifest')
    # The offline prototype deliberately has no vendor adapter or live-evidence claim path.
    if manifest['origin'] == 'native_live':
        raise ValueError('native_live evidence unsupported by constructed prototype adapters')
    provenance = manifest['provenance']
    if manifest['origin'] == 'derived_mutation':
        if not provenance['source_manifest_sha256'] or not provenance['transformation']:
            raise ValueError('derived mutation requires source digest and transformation')
    elif provenance['source_manifest_sha256'] is not None or provenance['transformation'] is not None:
        raise ValueError('constructed original must not claim a derivation')
    artifacts = manifest['artifacts']
    if len(artifacts)>256:
        raise ValueError('bundle exceeds 256-artifact prototype limit')
    unique(artifacts, 'id', 'manifest')
    unique(artifacts, 'path', 'manifest')
    dependencies(artifacts)
    by_path = {a['path']: a for a in artifacts}
    by_id = {a['id']: a for a in artifacts}
    if 'manifest.json' in by_path:
        raise ValueError('manifest must not inventory itself')
    actual = set()
    for path in root.rglob('*'):
        if path.is_symlink():
            raise ValueError('symlink in evidence bundle')
        if path.is_dir():
            continue
        if not path.is_file():
            raise ValueError('special file in evidence bundle')
        actual.add(path.relative_to(root).as_posix())
    if actual != set(by_path) | {'manifest.json'}:
        raise ValueError(f"inventory mismatch; missing={sorted(set(by_path)-actual)} undeclared={sorted(actual-set(by_path)-{'manifest.json'})}")
    for artifact in artifacts:
        data = safe_read(root, artifact['path'])
        if len(data) != artifact['size_bytes'] or digest(data) != artifact['sha256']:
            raise ValueError(f"integrity mismatch: {artifact['id']}")
    expected_path, observer_path = manifest['expectations_path'], manifest['observer_path']
    for name, role in [(expected_path, 'expected'), (observer_path, 'observer')]:
        if name not in by_path or by_path[name]['role'] != role:
            raise ValueError(f'{role} input is not inventoried under correct role')
    native = safe_path(root, manifest['native_package'])
    index_path = 'native/decode.json'
    if index_path not in by_path or by_path[index_path]['role'] != 'native':
        raise ValueError('missing native-only index')
    index = safe_json(root, 'native/decode.json')
    if set(index) != {'format', 'artifacts'} or index['format'] != manifest['decoder']['format']:
        raise ValueError('native decoder identity mismatch')
    if not isinstance(index['artifacts'], list):
        raise ValueError('native artifacts must be array')
    seen = set()
    for a in index['artifacts']:
        if set(a) != {'id','path','sha256','size_bytes','depends_on'}:
            raise ValueError('native decoder metadata may only contain artifact identity')
        safe_path(native, a['path'])
        p = 'native/' + a['path']
        full = by_path.get(p)
        if full is None or full['role'] != 'native' or {k:v for k,v in full.items() if k!='role' and k!='path'} != {k:v for k,v in a.items() if k!='path'}:
            raise ValueError('native artifact inventory differs from manifest')
        seen.add(p)
    if seen != {a['path'] for a in artifacts if a['role'] == 'native'} - {index_path}:
        raise ValueError('native-only package coverage mismatch')
    if any(not a['path'].startswith('native/') for a in artifacts if a['role']=='native'):
        raise ValueError('native artifacts must be under native-only root')
    if any(a['path'].startswith('native/') for a in artifacts if a['role']!='native'):
        raise ValueError('answer-key or other nonnative file in decoder package')
    expected = safe_json(root, expected_path)
    observer = safe_json(root, observer_path)
    validate_named(expected, 'assertions')
    validate_named(observer, 'observer')
    unique(expected['assertions'], 'id', 'expectations')
    unique(observer['events'], 'id', 'observer')
    obs = {e['id']:e for e in observer['events']}
    if manifest['execution']['native_sessions'] != len({e['session_id'] for e in observer['events']}):
        raise ValueError('native_sessions differs from independently specified observed session population')
    if manifest['execution']['scenario_runs'] != 1:
        raise ValueError('prototype bundle represents one constructed scenario-run fixture pack')
    for a in expected['assertions']:
        unique(a['fields'], 'name', f"assertion {a['id']}")
        if a['subject'] == 'writer_behavior':
            raise ValueError('constructed fixtures cannot establish writer behavior')
        if any(oid not in obs for oid in a['observation_ids']):
            raise ValueError('dangling observation ID')
        if a['execution']=='valid' and not a['observation_ids']:
            raise ValueError('exercised assertion requires independent observations')
        referenced = [obs[oid] for oid in a['observation_ids']]
        for o in referenced:
            if o['boundary'] != a['boundary'] or o['session_id'] != a['session_id'] or o['event_id'] != a['event_id']:
                raise ValueError('observation mapping/boundary differs from assertion')
        for f in a['fields']:
            name = f['name']
            candidates = [o['fields'][name] for o in referenced if name in o['fields']]
            if name in ('id','session_id'):
                candidates += [o['event_id' if name=='id' else name] for o in referenced]
            if a['execution']=='valid' and not any(canonical(c)==canonical(f['expected']) for c in candidates):
                raise ValueError(f"expectation not grounded in observation: {a['id']} {name}")
        inspection = a['inspection']
        if inspection['state'] != 'uninspected' and not inspection['evidence_ids']:
            raise ValueError('inspection requires evidence artifacts')
        for aid in inspection['evidence_ids']:
            if aid not in by_id or by_id[aid]['role'] != 'provenance':
                raise ValueError('inspection evidence missing or wrong role')
        if inspection['state']=='present' and not inspection['locators']:
            raise ValueError('present inspection requires native locators')
        for loc in inspection['locators']:
            if loc.get('artifact_id') not in by_id or by_id[loc['artifact_id']]['role']!='native':
                raise ValueError('inspection locator lacks native source')
    return manifest, expected, observer
