"""Run with python3 -m session_bench; all commands are offline."""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import time

from . import __version__
from .atlas import render_atlas, validate_atlas
from .bundle import canonical, digest, read_json, validate_bundle, validate_registry, validate_result
from .campaign_plan import campaign_plan_summary
from .evaluate import evaluate_bundle
from .fixtures import build_fixture
from .isolation import isolated_decode
from .result_contract import semantic_sha256
from .l0_controller import dry_run as l0_dry_run
from .l0_controller import L0Controller, LocalCodexRunner
from .l0_preflight import QuotaSnapshot


def write_output(out, files, source=None):
    out = Path(out).resolve()
    if source and out.is_relative_to(Path(source).resolve()):
        raise ValueError('output must be outside the input evidence/package')
    if out.exists() and any(out.iterdir()):
        raise ValueError('output directory must be empty; preserve earlier evaluations')
    out.mkdir(parents=True, exist_ok=True)
    for name, value in files.items():
        (out/name).write_bytes(value if isinstance(value, bytes) else value.encode('utf-8'))


def render(result, receipt=None):
    validate_result(result)
    receipt_label = 'receiptless; semantic result unverified.'
    if receipt is not None:
        if not isinstance(receipt, dict) or receipt.get('semantic_sha256') != semantic_sha256(result):
            raise ValueError('receipt semantic digest mismatch')
        receipt_label = f"verified (semantic_sha256 `{receipt['semantic_sha256']}`)."
    def escape(s):
        return str(s).replace('&','&amp;').replace('<','&lt;').replace('>','&gt;').replace('|','\\|').replace('\n',' ')
    lines=['# Constructed measurement-system report', '',
           '**No vendor result or qualification.**', '',
           f"Evaluation: `{result['evaluation_id']}`", '',
           f"Receipt: **{receipt_label}**", '',
           f"Capture: `{escape(result['capture_id'])}`; evidence: **{result['evidence_state']}**; origin: **{result['origin']}**.", '',
           '| Scenario | Reconstructed assertions / declared assertions | State |', '|---|---:|---|']
    for m in result['metrics']:
        lines.append(f"| {escape(m['scope'])} | {m['numerator']} / {m['denominator']} | {m['state']} |")
    lines += ['', '| Assertion | Result | Outcome | Findings |', '|---|---|---|---|']
    for r in result['rows']:
        lines.append(f"| {escape(r['id'])} | {r['state']} | {r['outcome']} | {escape(', '.join(r['findings']))} |")
    lines += ['', f"Unknown records: {result['unknown_records']}. Decoder diagnostics: {len(result['diagnostics'])}.", '',
              'Field values, observation references, source locators, and integrity digests are in results.json.',
              'Native continuation, cancellation, crash recovery, and vendor compatibility have not been tested.', '']
    return '\n'.join(lines)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version',action='version',version=__version__)
    subs=parser.add_subparsers(dest='command',required=True)
    for command in ('validate-bundle','validate-registry','decode','evaluate','render','collect'):
        sub=subs.add_parser(command)
        sub.add_argument('input',type=Path)
        if command in ('decode','evaluate','render'):
            sub.add_argument('--out',type=Path,required=True)
    validate_atlas_parser=subs.add_parser('validate-atlas', help='validate the independent format atlas')
    validate_atlas_parser.add_argument('input', type=Path)
    render_atlas_parser=subs.add_parser('render-atlas', help='render a dated atlas snapshot')
    render_atlas_parser.add_argument('input', type=Path)
    render_atlas_parser.add_argument('--out', type=Path, required=True)
    render_atlas_parser.add_argument('--as-of', required=True)
    campaign_parser=subs.add_parser('validate-campaign-plan', help='validate an offline campaign preparation plan')
    campaign_parser.add_argument('input', type=Path)
    campaign_parser.add_argument('--as-of', help='independent authorization timestamp required for ready plans')
    fixture=subs.add_parser('make-fixture')
    fixture.add_argument('--out',type=Path,required=True)
    fixture.add_argument('--format',choices=['constructed-jsonl-v1','constructed-sqlite-v1'],default='constructed-jsonl-v1')
    fixture.add_argument('--mutation')
    l0 = subs.add_parser('l0-preflight', help='validate and preview the bounded L0 controller')
    l0.add_argument('--plan', type=Path, required=True)
    l0.add_argument('--scratch', type=Path, required=True)
    l0.add_argument('--mcp-name', action='append', default=[])
    l0.add_argument('--dry-run', action='store_true')
    l0.add_argument('--sibling', type=Path)
    l0.add_argument('--quota-used-percent', type=float)
    l0.add_argument('--quota-observed-at')
    args=parser.parse_args(argv)
    try:
        if args.command=='collect':
            raise ValueError('live collection is not implemented or authorized; stop before L0/F0')
        if args.command=='l0-preflight':
            plan = read_json(args.plan)
            if args.dry_run:
                print(json.dumps(l0_dry_run(plan, args.mcp_name, args.scratch), ensure_ascii=False, sort_keys=True))
            else:
                if args.sibling is None or args.quota_used_percent is None or not args.quota_observed_at:
                    raise ValueError('actual preflight requires --sibling, --quota-used-percent, and --quota-observed-at')
                started = time.monotonic()
                contract = plan['limits']['quota']
                quota = QuotaSnapshot(contract['source'], args.quota_observed_at,
                    args.quota_used_percent, contract['baseline_used_percent'], started, time.monotonic())
                result = L0Controller(plan, LocalCodexRunner()).preflight(
                    args.scratch, args.sibling, quota=quota, now_monotonic=time.monotonic())
                safe = {key: result[key] for key in ('override_fingerprint', 'resolved_fingerprint', 'mcp_names', 'sandbox', 'plan_sha256')}
                print(json.dumps(safe, ensure_ascii=False, sort_keys=True))
            return 0
        if args.command=='make-fixture':
            build_fixture(args.out,args.format,args.mutation)
            print(f'constructed fixture: {args.out}')
        elif args.command=='validate-bundle':
            manifest,_,_=validate_bundle(args.input)
            print(json.dumps({'evidence':'valid','capture_status':manifest['capture']['status'],'origin':manifest['origin']},sort_keys=True))
        elif args.command=='validate-registry':
            validate_registry(read_json(args.input));print('valid registry')
        elif args.command=='validate-atlas':
            atlas = validate_atlas(read_json(args.input))
            print(json.dumps({'atlas_id': atlas['atlas_id'], 'entries': len(atlas['entries']),
                              'edition_status': atlas['edition_status']}, sort_keys=True))
        elif args.command=='render-atlas':
            atlas = read_json(args.input)
            rendered = render_atlas(atlas, args.as_of)
            if args.out.resolve() == args.input.resolve():
                raise ValueError('atlas output must differ from the machine-readable input')
            args.out.parent.mkdir(parents=True, exist_ok=True)
            temporary = None
            try:
                with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=args.out.parent,
                                                 prefix=f'.{args.out.name}.', delete=False) as stream:
                    temporary = Path(stream.name)
                    stream.write(rendered)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, args.out)
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
            print(json.dumps({'atlas_id': atlas['atlas_id'], 'as_of': args.as_of,
                              'output': str(args.out)}, sort_keys=True))
        elif args.command=='validate-campaign-plan':
            print(json.dumps(campaign_plan_summary(read_json(args.input), as_of=args.as_of), sort_keys=True))
        elif args.command=='decode':
            decoded=isolated_decode(args.input)
            write_output(args.out,{'decoded.json':canonical(decoded)+b'\n'},args.input)
        elif args.command=='evaluate':
            result,decoded=evaluate_bundle(args.input)
            receipt = {'semantic_sha256': semantic_sha256(result),
                       'evaluation_id': result['evaluation_id'],
                       'manifest_sha256': result['manifest_sha256'],
                       'implementation_sha256': result['implementation_sha256']}
            write_output(args.out,{'results.json':canonical(result)+b'\n','decoded.json':canonical(decoded)+b'\n',
                                   'report.md':render(result, receipt),
                                   'receipt.json':canonical(receipt)+b'\n'},args.input)
            print(json.dumps({'evaluation_id':result['evaluation_id'],'evidence_state':result['evidence_state']},sort_keys=True))
            # Valid measured failures are successful evaluations, not broken evidence.
            return 0 if result['evidence_state']=='valid' else 2
        elif args.command=='render':
            result = read_json(args.input)
            receipt_path = args.input.parent / 'receipt.json'
            receipt = read_json(receipt_path) if receipt_path.exists() else None
            write_output(args.out,{'report.md':render(result, receipt)},args.input.parent)
        return 0
    except (ValueError,OSError,KeyError,TypeError,RecursionError,RuntimeError) as exc:
        print(f'session-bench: {exc}',file=sys.stderr)
        return 2


if __name__=='__main__':
    raise SystemExit(main())
