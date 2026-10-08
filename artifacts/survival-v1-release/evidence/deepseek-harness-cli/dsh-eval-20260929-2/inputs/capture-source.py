#!/usr/bin/env python3
"""Capture a bounded DSH two-turn workload using existing prepaid credit.

No scores or publication claims are produced. Authentication remains in process
memory, never in the run package; only a fresh isolated DSH home is inventoried.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from session_bench.adapters.deepseek_harness import build_headless_launch_plan, run_headless
from session_bench.workload_instance import instantiate_workload
from session_bench.dsh_native import read_physical


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path, value):
    with path.open('x', encoding='utf-8') as handle:
        json.dump(value, handle, ensure_ascii=False, sort_keys=True, indent=2)
        handle.write('\n')


def balance(key):
    request = urllib.request.Request('https://api.deepseek.com/user/balance', headers={
        'Authorization': 'Bearer ' + key, 'Accept': 'application/json'})
    with urllib.request.urlopen(request, timeout=20) as response:
        result = json.load(response)
    amounts = [Decimal(row['total_balance']) for row in result['balance_infos'] if row['currency'] == 'USD']
    if not result.get('is_available') or len(amounts) != 1:
        raise ValueError('existing USD balance is unavailable')
    return amounts[0]


def capture(attempt, *, timeout=180):
    import yaml
    # Only the named provider credential is used; other credential records and
    # private session stores are neither copied nor opened.
    credential_path = Path.home() / '.dsh/.credentials.yaml'
    key = yaml.safe_load(credential_path.read_text())['refs']['DEEPSEEK_API_KEY']
    initial_balance = balance(key)
    if initial_balance < Decimal('0.25'):
        raise ValueError('less than USD 0.25 existing credit; no top-up attempted')
    template_root = ROOT / 'fixtures/scenarios/survival-v1/workload'
    workload, workload_env = instantiate_workload(json.loads((template_root / 'workload.json').read_text()), attempt)
    run = ROOT / 'artifacts/survival-v1-runs' / attempt
    run.mkdir(parents=True, exist_ok=False)
    workspace = run / 'workspace'
    workspace.mkdir()
    fixture = workspace / 'fixture_project'
    shutil.copytree(template_root / 'fixture_project', fixture)
    version = subprocess.check_output(['dsh', '--version'], text=True, timeout=15).strip()
    plan = build_headless_launch_plan(run, workspace, identity_probe={'product': 'DeepSeek Harness', 'version': version})
    write(run / 'workload.json', workload)
    protected = {name: digest(fixture / name) for name in ('bench_check.py',)}
    before = digest(fixture / 'checkout.py')
    state = {'schema_version': 'session-bench-dsh-capture-v1', 'attempt_id': attempt,
             'configuration_id': 'deepseek-harness-cli', 'version': version,
             'started_at': datetime.now(timezone.utc).isoformat(),
             'initial_balance_usd': str(initial_balance), 'between_turn_spend_stop_usd': '0.25',
             'hard_billing_cap': False, 'timeout_seconds_per_turn': timeout,
             'before_sha256': before, 'turns': [], 'score_eligible': False,
             'observer_limit': 'stdout is a vendor projection; helper ledger and filesystem are separate observations'}
    write(run / 'capture-start.json', state)
    session_id = None
    try:
        for index, turn in enumerate(workload['turns'], 1):
            current = balance(key)
            if initial_balance - current >= Decimal('0.25'):
                raise ValueError('between-turn credit consumption stop reached')
            def runner(argv, *, env, cwd):
                execution_env = {**os.environ, **env, **workload_env, 'DEEPSEEK_API_KEY': key}
                with (run / f'r{index}.stdout.jsonl').open('xb') as out, (run / f'r{index}.stderr.txt').open('xb') as err:
                    child = subprocess.Popen(argv, cwd=cwd, env=execution_env, stdout=out, stderr=err, start_new_session=True)
                    try:
                        return child.wait(timeout=timeout)
                    except subprocess.TimeoutExpired:
                        import signal
                        os.killpg(child.pid, signal.SIGTERM)
                        try:
                            child.wait(timeout=10)
                        except subprocess.TimeoutExpired:
                            os.killpg(child.pid, signal.SIGKILL)
                            child.wait()
                        raise
            result = run_headless(plan, turn['text'], runner=runner, resume_session_id=session_id)
            stream = (run / f'r{index}.stdout.jsonl').read_text()
            if key in stream or key in (run / f'r{index}.stderr.txt').read_text():
                raise ValueError('credential found in captured output; package cannot be used')
            rows = [json.loads(line) for line in stream.splitlines() if line.strip()]
            sessions = {row['sessionId'] for row in rows if row.get('type') == 'session'}
            finals = [row for row in rows if row.get('type') == 'final']
            state['turns'].append({'turn_id': turn['id'], 'exit_code': result, 'session_ids': sorted(sessions),
                                   'stdout_sha256': digest(run / f'r{index}.stdout.jsonl'),
                                   'canary_present': bool(finals and finals[-1].get('text', '').rstrip().endswith(turn['response_canary']))})
            write(run / f'r{index}.capture.json', state['turns'][-1])
            if result != 0 or len(sessions) != 1 or not state['turns'][-1]['canary_present']:
                raise ValueError('turn did not complete with one session and the required response canary')
            observed = next(iter(sessions))
            if session_id is not None and observed != session_id:
                raise ValueError('continuation changed session identity')
            session_id = observed
            native = list(plan.native_root.glob('*/*/session.v4.jsonl')) + list(plan.native_root.glob('*/*/session.v4.jsonl.zstd'))
            if len(native) != 1 or native[0].is_symlink():
                raise ValueError('v4 single-session capture boundary not established')
            logical, physical = read_physical(native[0])
            header = json.loads(logical.splitlines()[0])
            if header.get('id') != session_id:
                raise ValueError('native header differs from stdout session identity')
            shutil.copyfile(native[0], run / f'r{index}.native{native[0].name.removeprefix("session.v4")}')
            write(run / f'r{index}.physical.json', physical)
            if any(digest(fixture / name) != value for name, value in protected.items()):
                raise ValueError('protected helper changed')
            if index == 1 and digest(fixture / 'checkout.py') != before:
                raise ValueError('R1 changed target before R2 was submitted')
        state['status'] = 'captured_pending_qualification'
    except Exception as error:
        state['status'] = 'invalid_or_blocked'
        state['error_type'] = type(error).__name__
        # Avoid exception messages from HTTP/client errors which can carry URLs
        # or provider secrets. Our own validation failures contain no secrets.
        if isinstance(error, ValueError):
            state['reason'] = str(error)
    finally:
        state['after_sha256'] = digest(fixture / 'checkout.py')
        state['finished_at'] = datetime.now(timezone.utc).isoformat()
        try:
            state['final_balance_usd'] = str(balance(key))
            state['observed_balance_delta_usd'] = str(initial_balance - Decimal(state['final_balance_usd']))
        except Exception:
            state['final_balance_unavailable'] = True
        write(run / 'capture-result.json', state)
    print(json.dumps({'attempt_id': attempt, 'status': state['status'], 'turns': len(state['turns']),
                      'reason': state.get('reason'), 'error_type': state.get('error_type'),
                      'observed_balance_delta_usd': state.get('observed_balance_delta_usd')}))
    return 0 if state['status'] == 'captured_pending_qualification' else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--attempt-id', required=True)
    parser.add_argument('--timeout', type=int, default=180)
    args = parser.parse_args()
    if not 1 <= args.timeout <= 300:
        parser.error('timeout must be 1..300 seconds')
    raise SystemExit(capture(args.attempt_id, timeout=args.timeout))
