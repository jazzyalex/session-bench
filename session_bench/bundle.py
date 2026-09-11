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


def validate_constructed_subject(subject, format):
    expected = {'harness':'constructed','version':'1','surface':'cli',
                'mode':'offline-fixture','os':'synthetic','model':'none',
                'configuration':'default','artifact_family':format,'schema_version':'1'}
    if format not in ('constructed-jsonl-v1','constructed-sqlite-v1') or subject != expected:
        raise ValueError('prototype requires exact constructed subject and artifact family')


def validate_live_subject(subject, format):
    if format != 'codex-rollout-v1':
        raise ValueError('native live prototype requires the Codex rollout adapter')
    if subject['harness'] != 'codex-cli' or subject['surface'] != 'cli':
        raise ValueError('native live subject must identify Codex CLI')
    if subject['mode'] != 'interactive_local' or subject['configuration'] != 'bounded':
        raise ValueError('native live subject must identify the reviewed bounded interactive mode')
    if subject['artifact_family'] != 'codex-rollout-jsonl':
        raise ValueError('native live artifact family mismatch')


def validate_live_binding(root, manifest, artifacts_by_id):
    """Verify that a native-live claim is joined to its immutable run evidence."""
    binding = manifest.get('live_binding')
    if not isinstance(binding, dict):
        raise ValueError('native live capture requires a live evidence binding')
    if binding['capture_id'] != manifest['capture_id']:
        raise ValueError('live binding capture identity differs from manifest')
    for key in ('plan_artifact_id', 'capture_evidence_artifact_id', 'ledger_artifact_id',
                'resolved_config_artifact_id'):
        artifact = artifacts_by_id.get(binding[key])
        if artifact is None or artifact['role'] != 'provenance':
            raise ValueError(f"live binding {key} is not an inventoried provenance artifact")

    def provenance_json(artifact_id):
        artifact = artifacts_by_id[artifact_id]
        try:
            value = safe_json(root, artifact['path'])
        except (OSError, UnicodeDecodeError, ValueError, TypeError) as exc:
            raise ValueError(f'live provenance artifact is not valid JSON: {artifact_id}') from exc
        if not isinstance(value, dict):
            raise ValueError(f'live provenance artifact must be an object: {artifact_id}')
        return value

    plan = provenance_json(binding['plan_artifact_id'])
    plan_payload = plan.get('plan')
    if not isinstance(plan_payload, dict) or plan.get('plan_sha256') != binding['plan_sha256'] or digest(canonical(plan_payload)) != binding['plan_sha256']:
        raise ValueError('live plan digest does not match inventoried plan artifact')
    from .live_plan import plan_sha256, validate_live_plan
    validate_live_plan(plan_payload)
    if plan_sha256(plan_payload) != binding['plan_sha256']:
        raise ValueError('live plan semantic identity does not match binding')

    capture = provenance_json(binding['capture_evidence_artifact_id'])
    if artifacts_by_id[binding['capture_evidence_artifact_id']]['sha256'] != binding['capture_evidence_sha256']:
        raise ValueError('capture evidence digest does not match inventoried artifact')
    if (capture.get('attempt_id') != binding['attempt_id']
            or capture.get('scenario_id') != binding['scenario_id']
            or capture.get('scenario_run_id') != binding['scenario_run_id']
            or capture.get('resolved_config_fingerprint') != binding['resolved_config_fingerprint']):
        raise ValueError('capture evidence identity does not match live binding')
    sessions = capture.get('native_session_ids')
    if not isinstance(sessions, list) or binding['native_session_id'] not in sessions:
        raise ValueError('capture evidence does not contain the claimed native session')

    resolved = provenance_json(binding['resolved_config_artifact_id'])
    if artifacts_by_id[binding['resolved_config_artifact_id']]['sha256'] != binding['resolved_config_sha256']:
        raise ValueError('resolved configuration digest does not match inventoried artifact')
    if set(resolved) != {'override_argv', 'launch_argv', 'override_fingerprint', 'features', 'mcps'}:
        raise ValueError('resolved configuration evidence has an invalid shape')
    from .l0_preflight import build_effective_config, verify_resolved_config
    if not isinstance(resolved['mcps'], dict) or any(type(value) is not bool for value in resolved['mcps'].values()):
        raise ValueError('resolved MCP evidence is malformed')
    config = build_effective_config(disable_mcps=tuple(resolved['mcps']))
    if resolved['override_argv'] != list(config.argv) or resolved['override_fingerprint'] != config.override_fingerprint:
        raise ValueError('resolved configuration override preimage differs from approved vector')
    launch = resolved['launch_argv']
    scratch_path = launch[-5] if isinstance(launch, list) and len(launch) >= 7 else None
    expected_tail = ['--no-alt-screen', '-C', scratch_path,
                     '--sandbox', 'workspace-write', '--ask-for-approval', 'never']
    if (not isinstance(launch, list) or launch[:1] != ['/opt/homebrew/bin/codex']
            or not isinstance(scratch_path, str) or not Path(scratch_path).is_absolute()
            or launch[1:1 + len(config.argv)] != list(config.argv)
            or launch[1 + len(config.argv):] != expected_tail):
        raise ValueError('resolved launch invocation differs from bounded controller vector')
    recomputed_config = verify_resolved_config(config, features=resolved['features'], mcps=resolved['mcps'])
    if recomputed_config != binding['resolved_config_fingerprint']:
        raise ValueError('resolved configuration fingerprint cannot be reproduced')

    ledger = provenance_json(binding['ledger_artifact_id'])
    if artifacts_by_id[binding['ledger_artifact_id']]['sha256'] != binding['ledger_sha256']:
        raise ValueError('ledger digest does not match inventoried artifact')
    if ledger.get('plan_sha256') != binding['plan_sha256'] or ledger.get('gate_id') != 'codex-cli-f0':
        raise ValueError('ledger is not bound to the claimed plan')
    attempts = ledger.get('attempts')
    from .live_ledger import validate_capture_evidence, validate_live_ledger
    validate_live_ledger(ledger, plan_payload)
    matching = [item for item in attempts
                if item['attempt_id'] == binding['attempt_id']
                and item['scenario_id'] == binding['scenario_id']
                and item['scenario_run_id'] == binding['scenario_run_id']
                and binding['native_session_id'] in item['native_session_ids']]
    if len(matching) != 1:
        raise ValueError('ledger does not contain the claimed attempt and native session')
    attempt = matching[0]
    if attempt['config_identity'] != binding['resolved_config_fingerprint']:
        raise ValueError('ledger configuration identity does not match live binding')
    validate_capture_evidence(capture, plan_payload, attempt=attempt)
    plan_subject = plan_payload['subject']
    manifest_subject = manifest['subject']
    for key in ('harness', 'version', 'surface', 'mode', 'os', 'artifact_family'):
        if manifest_subject[key] != plan_subject[key]:
            raise ValueError(f'native live subject {key} differs from bound plan')
    return binding


def validate_registry(registry):
    validate_named(registry, 'registry')
    unique(registry['surfaces'], 'id', 'registry')
    for row in registry['surfaces']:
        if row['status'] != 'constructed_only':
            raise ValueError('candidate or measured surface unsupported by offline prototype')
        validate_constructed_subject(row['subject'], row['id'])
        if any(row[key] is not None for key in ('source_inspected_at','live_tested_at','next_inspection_due')):
            raise ValueError('constructed registry cannot claim inspection or live dates')
    return registry


def validate_result(result):
    from .result_contract import validate_result_semantics
    validate_named(result, 'result')
    validate_result_semantics(result)
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
    if manifest['origin'] == 'native_live':
        validate_live_subject(manifest['subject'], manifest['decoder']['format'])
    else:
        validate_constructed_subject(manifest['subject'], manifest['decoder']['format'])
    provenance = manifest['provenance']
    if manifest['origin'] == 'derived_mutation':
        if not provenance['source_manifest_sha256'] or not provenance['transformation']:
            raise ValueError('derived mutation requires source digest and transformation')
    elif provenance['source_manifest_sha256'] is not None or provenance['transformation'] is not None:
        raise ValueError('original capture must not claim a derivation')
    if manifest['origin'] == 'native_live' and provenance['privacy_review'] != 'local-f0-synthetic-scan':
        raise ValueError('native live capture requires the F0 privacy scan')
    artifacts = manifest['artifacts']
    if len(artifacts)>256:
        raise ValueError('bundle exceeds 256-artifact prototype limit')
    unique(artifacts, 'id', 'manifest')
    unique(artifacts, 'path', 'manifest')
    dependencies(artifacts)
    by_path = {a['path']: a for a in artifacts}
    by_id = {a['id']: a for a in artifacts}
    if manifest['origin'] == 'native_live':
        validate_live_binding(root, manifest, by_id)
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
    if manifest['origin'] == 'native_live':
        observed_sessions = {event['session_id'] for event in observer['events']}
        if manifest['live_binding']['native_session_id'] not in observed_sessions:
            raise ValueError('bound native session differs from independently observed session population')
    obs = {e['id']:e for e in observer['events']}
    if manifest['execution']['native_sessions'] != len({e['session_id'] for e in observer['events']}):
        raise ValueError('native_sessions differs from independently specified observed session population')
    if manifest['execution']['scenario_runs'] != 1:
        raise ValueError('prototype bundle represents one scenario run')
    # Every primary observation must be represented exactly once in the scored
    # population. Supporting helper/file observations are deliberately separate.
    primary = {oid for oid, o in obs.items() if o['population_role']=='primary_scored'}
    membership = {oid: [] for oid in primary}
    primary_keys = set()
    for assertion in expected['assertions']:
        if any(oid not in obs for oid in assertion['observation_ids']):
            raise ValueError('dangling observation ID')
        if assertion['assertion_role']=='primary':
            key=(assertion['session_id'],assertion['event_id'],assertion['boundary'])
            if key in primary_keys:
                raise ValueError('duplicate primary assertion for observed event boundary')
            primary_keys.add(key)
            primary_refs = set(assertion['observation_ids']) & primary
            if not primary_refs:
                raise ValueError('primary assertion requires primary scored observation')
            for oid in primary_refs:
                membership[oid].append(assertion['id'])
    if any(len(owners)!=1 for owners in membership.values()):
        raise ValueError('primary observation population requires exactly one primary assertion')
    # Conflicts among primary observations cannot be hidden by selecting a source.
    observed_fields = {}
    for o in obs.values():
        if o['population_role']!='primary_scored':
            continue
        for name, value in o['fields'].items():
            key=(o['session_id'],o['event_id'],o['boundary'],name)
            if key in observed_fields and canonical(observed_fields[key])!=canonical(value):
                raise ValueError('conflicting primary observations')
            observed_fields[key]=value
    for a in expected['assertions']:
        unique(a['fields'], 'name', f"assertion {a['id']}")
        if manifest['origin'] != 'native_live' and a['subject'] == 'writer_behavior':
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
            if len({canonical(c) for c in candidates})>1:
                raise ValueError('conflicting observations for scored field')
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
    from .locators import validate_inspections
    validate_inspections(root, manifest, expected)
    return manifest, expected, observer
