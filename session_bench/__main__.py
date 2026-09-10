"""Run with python3 -m session_bench; all commands are offline."""
import argparse
import json
from pathlib import Path
import sys

from . import __version__
from .bundle import canonical, digest, read_json, validate_bundle, validate_registry, validate_result
from .evaluate import evaluate_bundle
from .fixtures import build_fixture
from .isolation import isolated_decode


def write_output(out, files, source=None):
    out = Path(out).resolve()
    if source and out.is_relative_to(Path(source).resolve()):
        raise ValueError('output must be outside the input evidence/package')
    if out.exists() and any(out.iterdir()):
        raise ValueError('output directory must be empty; preserve earlier evaluations')
    out.mkdir(parents=True, exist_ok=True)
    for name, value in files.items():
        (out/name).write_bytes(value if isinstance(value, bytes) else value.encode('utf-8'))


def render(result):
    validate_result(result)
    def escape(s):
        return str(s).replace('&','&amp;').replace('<','&lt;').replace('>','&gt;').replace('|','\\|').replace('\n',' ')
    lines=['# Constructed measurement-system report', '',
           '**No vendor result or qualification.**', '',
           f"Evaluation: `{result['evaluation_id']}`", '',
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
    fixture=subs.add_parser('make-fixture')
    fixture.add_argument('--out',type=Path,required=True)
    fixture.add_argument('--format',choices=['constructed-jsonl-v1','constructed-sqlite-v1'],default='constructed-jsonl-v1')
    fixture.add_argument('--mutation')
    args=parser.parse_args(argv)
    try:
        if args.command=='collect':
            raise ValueError('live collection is not implemented or authorized; stop before L0/F0')
        if args.command=='make-fixture':
            build_fixture(args.out,args.format,args.mutation)
            print(f'constructed fixture: {args.out}')
        elif args.command=='validate-bundle':
            manifest,_,_=validate_bundle(args.input)
            print(json.dumps({'evidence':'valid','capture_status':manifest['capture']['status'],'origin':manifest['origin']},sort_keys=True))
        elif args.command=='validate-registry':
            validate_registry(read_json(args.input));print('valid registry')
        elif args.command=='decode':
            decoded=isolated_decode(args.input)
            write_output(args.out,{'decoded.json':canonical(decoded)+b'\n'},args.input)
        elif args.command=='evaluate':
            result,decoded=evaluate_bundle(args.input)
            write_output(args.out,{'results.json':canonical(result)+b'\n','decoded.json':canonical(decoded)+b'\n',
                                   'report.md':render(result),'receipt.json':canonical({'semantic_sha256':digest(canonical(result)),
                                   'evaluation_id':result['evaluation_id'],'manifest_sha256':result['manifest_sha256'],
                                   'implementation_sha256':result['implementation_sha256']})+b'\n'},args.input)
            print(json.dumps({'evaluation_id':result['evaluation_id'],'evidence_state':result['evidence_state']},sort_keys=True))
            # Valid measured failures are successful evaluations, not broken evidence.
            return 0 if result['evidence_state']=='valid' else 2
        elif args.command=='render':
            write_output(args.out,{'report.md':render(read_json(args.input))},args.input.parent)
        return 0
    except (ValueError,OSError,KeyError,TypeError,RecursionError) as exc:
        print(f'session-bench: {exc}',file=sys.stderr)
        return 2


if __name__=='__main__':
    raise SystemExit(main())
