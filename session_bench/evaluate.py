"""Deterministic field assertions over a native-only reconstruction."""
from pathlib import Path

from . import __version__
from .bundle import canonical, digest, validate_bundle, validate_result
from .isolation import isolated_decode
from .locators import validate_sources

MISSING = object()


def lookup(event, name):
    value = event
    for part in name.split('.'):
        if not isinstance(value, dict) or part not in value:
            return MISSING
        value = value[part]
    return value


def compare(actual, expected, rule):
    if rule == 'presentation_unknown':
        return 'unresolved'
    if actual is MISSING:
        return 'fail'
    if rule == 'text_lf':
        if not isinstance(actual, str) or not isinstance(expected, str):
            return 'unresolved'
        # The only equivalence here is CRLF -> LF, never trim code or repair wraps.
        return 'pass' if actual.replace('\r\n','\n') == expected.replace('\r\n','\n') else 'fail'
    if rule in ('exact', 'json'):
        # JSON object key ordering is insignificant; scalar types/array order are not.
        return 'pass' if canonical(actual) == canonical(expected) else 'fail'
    raise ValueError(f'unknown comparator: {rule}')


def implementation_digest():
    base = Path(__file__).resolve().parent
    files = sorted(base.glob('*.py')) + sorted((base.parent/'schemas'/'v1').glob('*.json'))
    return digest(canonical({p.relative_to(base.parent).as_posix():digest(p.read_bytes()) for p in files}))


def evaluate_bundle(root, *, decoder=isolated_decode):
    """Decode in an OS sandbox by default. Injectable decoder is for unit tests only."""
    root = Path(root)
    manifest, expected, observer = validate_bundle(root)
    manifest_bytes=(root/'manifest.json').read_bytes()
    decoded = decoder(root / manifest['native_package'])
    validate_sources(root, manifest, decoded)
    after, _, _ = validate_bundle(root)
    if canonical(after)!=canonical(manifest) or (root/'manifest.json').read_bytes()!=manifest_bytes:
        raise ValueError('evidence changed during evaluation')
    result = evaluate_decoded(manifest, expected, decoded, digest(manifest_bytes))
    return result, decoded


def evaluate_decoded(manifest, expected, decoded, manifest_sha):
    # The CLI never accepts user-supplied decoded output. Tests can inject decoder
    # faults to prove the distinction between preservation and decoder correctness.
    rows = []
    complete = manifest['capture']['roots_complete'] and manifest['observation']['complete']
    capture_valid = manifest['capture']['status']=='valid' and manifest['execution']['state']=='valid'
    unknown_codes = {'malformed_record','decode_error','unsupported_schema','unknown_record','missing_artifact','missing_dependency'}
    native_uncertain = any(d['code'] in unknown_codes for d in decoded['diagnostics'])
    for assertion in expected['assertions']:
        matches = [e for e in decoded['events'] if e['id']==assertion['event_id'] and e['session_id']==assertion['session_id']]
        executable = capture_valid and complete and assertion['execution']=='valid' and assertion['applicability'] in ('required','optional_supported')
        findings, fields = [], []
        locators = [e['locator'] for e in matches]
        known_kind = len(matches)==1 and matches[0]['kind'] in {'message','tool_call','tool_result','session','branch','attachment'}
        for field in assertion['fields']:
            actual = lookup(matches[0],field['name']) if len(matches)==1 else MISSING
            state = compare(actual, field['expected'], field['comparison']) if executable and known_kind else 'unresolved'
            fields.append({'name':field['name'],'state':state,'comparison':field['comparison'],
                           'expected':field['expected'],'actual':None if actual is MISSING else actual,'actual_present':actual is not MISSING})
        state, outcome = 'unresolved', 'unresolved'
        inspection = assertion['inspection']
        if not executable:
            findings.append('not_evaluable')
        elif not assertion['fields']:
            findings.append('zero_denominator')
        elif matches and inspection['state']=='absent':
            state = 'fail'
            findings.append('inspection_contradiction')
        elif len(matches)>1:
            state = 'fail'
            findings.append('duplicate')
        elif not matches:
            if inspection['state']=='present':
                state, outcome = 'fail','retained_decoder_incomplete'
                locators = inspection['locators']
                findings.append('decoder_omission')
            elif inspection['state']=='absent' and complete and not native_uncertain:
                state, outcome = 'fail','verified_absent'
                findings.append('loss')
            else:
                findings.append('absence_unverified')
        elif not known_kind:
            findings.append('unknown_record')
        elif any(f['state']=='fail' for f in fields):
            state = 'fail'
            if inspection['state']=='present':
                outcome = 'retained_decoder_incomplete'
                findings.append('decoder_mismatch')
            else:
                # Value disagreement is established; its writer/decoder cause is not.
                findings.append('contradicted')
        elif any(f['state']=='unresolved' for f in fields):
            findings.append('field_uncertainty')
        else:
            state, outcome = 'pass','retained_and_reconstructed'
        # Cross-record relationships must resolve, even if a literal ID matches.
        if executable and len(matches)==1:
            event = matches[0]
            for key, required_kind in [('call_id','tool_call'),('tool_call_id','tool_call'),('parent_id',None)]:
                ref = event['fields'].get(key)
                if ref is not None:
                    targets = [e for e in decoded['events'] if e['id']==ref and e['session_id']==event['session_id'] and (required_kind is None or e['kind']==required_kind)]
                    if len(targets)!=1:
                        state,outcome='unresolved','unresolved'
                        findings.append('unresolved_join')
            if event['kind']=='branch':
                branches={e['id']:e for e in decoded['events'] if e['kind']=='branch' and e['session_id']==event['session_id']}
                visited=set()
                current=event['id']
                while current in branches:
                    if current in visited:
                        state,outcome='unresolved','unresolved'
                        findings.append('branch_cycle')
                        break
                    visited.add(current)
                    current=branches[current]['fields'].get('parent_id')
        rows.append({'id':assertion['id'],'scenario':assertion['scenario'],'subject':assertion['subject'],
                     'applicability':assertion['applicability'],'execution':assertion['execution'],
                     'boundary':assertion['boundary'],'state':state,'outcome':outcome,'findings':sorted(set(findings)),
                     'fields':fields,'locators':locators,'observation_ids':assertion['observation_ids'],
                     'inspection_evidence_ids':inspection['evidence_ids']})
    # All predeclared assertions remain in denominator, including missing data.
    metrics = []
    for scenario in sorted({r['scenario'] for r in rows}):
        selected=[r for r in rows if r['scenario']==scenario]
        passed=sum(r['state']=='pass' for r in selected)
        total=len(selected)
        metrics.append({'scope':scenario,'unit':'assertions','numerator':passed,'denominator':total,
                        'state':'unresolved' if not total or any(r['state']=='unresolved' for r in selected) else 'pass' if passed==total else 'fail',
                        'assertion_ids':[r['id'] for r in selected]})
    if not rows:
        metrics.append({'scope':'all','unit':'assertions','numerator':0,'denominator':0,'state':'unresolved','assertion_ids':[]})
    result={'schema_version':'1.0-prototype','evaluation_id':'pending','run_id':manifest['run_id'],
            'capture_id':manifest['capture_id'],'manifest_sha256':manifest_sha,'decoder_version':__version__,
            'evaluator_version':__version__,'implementation_sha256':implementation_digest(),'decoded_sha256':digest(canonical(decoded)),'origin':manifest['origin'],
            'claim_scope':'constructed measurement-system test; no vendor qualification',
            'capture_status':manifest['capture']['status'],'evidence_state':'valid' if capture_valid else 'invalid',
            'rows':rows,'metrics':metrics,'diagnostics':decoded['diagnostics'],'unknown_records':decoded['unknown_records']}
    result['evaluation_id']='eval-'+digest(canonical({'result':result,'implementation_sha256':implementation_digest(),'decoded_sha256':digest(canonical(decoded))}))
    validate_result(result)
    return result
