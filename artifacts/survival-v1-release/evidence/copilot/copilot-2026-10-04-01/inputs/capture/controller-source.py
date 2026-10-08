#!/usr/bin/env python3
"""Capture one isolated Copilot attempt, preserving evidence even on failure.

Temporary roots are deliberately retained; no cleanup can destroy evidence.
"""
import argparse
import hashlib
import json
import os
import re
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from session_bench.workload_instance import instantiate_workload
from session_bench.copilot_capture_qualification import qualify_copilot_capture


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x') as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write('\n')


def inventory(root):
    result = {}
    for path in root.rglob('*'):
        if path.is_symlink():
            raise ValueError('symlink in fixture')
        if path.is_file():
            result[path.relative_to(root).as_posix()] = {'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
    return result


def safe_launch_receipt(args, *, fixture, env, native_root, isolated_home):
    """Record exact launch boundary without copying the injected credential."""
    allowed = {'PATH', 'TMPDIR', 'LANG', 'TERM', 'HOME', 'COPILOT_HOME', 'SB_SURVIVAL_V1_RUN_CANARY'}
    if set(env) - allowed != {'COPILOT_GITHUB_TOKEN'}:
        raise ValueError('unexpected inherited environment key')
    return {'argv': list(args), 'cwd': str(fixture), 'started_ns': time.time_ns(),
            'safe_environment': {key: value for key, value in env.items() if key in allowed},
            'secret_environment_variables': ['COPILOT_GITHUB_TOKEN'], 'credential_values_retained': False,
            'copilot_home_inventory_before': inventory(native_root),
            'isolated_home_inventory_before': inventory(isolated_home)}


def validate_stdout(raw, expected_model='gpt-6-luna'):
    from session_bench.copilot_capture_qualification import _stream
    rows = _stream(raw, 'Copilot independent stdout')
    responses = [row.get('data', {}) for row in rows if row.get('type') == 'assistant.message']
    if not responses or any(row.get('model') != expected_model for row in responses):
        raise ValueError('actual auto-routed model differs from qualified cohort; no route fallback')
    if any(row.get('type') in {'error', 'session.error'} for row in rows):
        raise ValueError('runtime error event; no retry')
    if not any(row.get('type') == 'result' and row.get('exitCode') == 0 for row in rows):
        raise ValueError('stdout lacks successful completion')
    return rows


def preserve(run, fixture, native):
    """Copy every fixture input before attempting qualification; never delete roots."""
    shutil.copytree(fixture, run / 'workspace/fixture_project')
    write(run / 'filesystem/after-state.json', {'files': inventory(run / 'workspace/fixture_project')})
    if native.exists():
        shutil.copytree(native, run / 'native' / native.name)
    return inventory(run / 'workspace/fixture_project') == inventory(fixture)


def capture(attempt):
    template_dir = ROOT / 'fixtures/scenarios/survival-v1/workload'
    template_bytes = (template_dir / 'workload.json').read_bytes()
    workload, canary_env = instantiate_workload(json.loads(template_bytes), attempt)
    run = ROOT / 'artifacts/v1-expanded-preparation/live-captures' / attempt
    run.mkdir(parents=True, exist_ok=False)
    temp = Path(tempfile.mkdtemp(prefix='session-bench-copilot-')).resolve()
    fixture = temp / 'fixture_project'
    shutil.copytree(template_dir / 'fixture_project', fixture)
    home = temp / 'home'; home.mkdir()
    copilot_home = temp / 'copilot'; copilot_home.mkdir()
    (run / 'workload_template.json').write_bytes(template_bytes)
    write(run / 'workload_instance.json', workload)
    initial = inventory(fixture)
    initial['.survival-observer.jsonl'] = {'existed_before_first_turn': False}
    write(run / 'filesystem/initial-state.json', {'files': initial})
    session = str(uuid.uuid4())
    executable = shutil.which('copilot')
    state = {'run_id': attempt, 'surface': 'copilot-cli', 'status': 'in_progress',
             'session_id': session, 'model_requested': 'auto',
             'cli_version': re.search(r'CLI ([0-9]+\.[0-9]+\.[0-9]+)', subprocess.check_output([executable, '--version'], text=True)).group(1),
             'execution_workspace_path': str(fixture), 'temporary_root_retained': str(temp),
             'auth_route': 'gh auth token -> COPILOT_GITHUB_TOKEN in-process; value not retained',
             'controller_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    (run / 'controller-source.py').write_bytes(Path(__file__).read_bytes())
    write(run / 'root-start.json', {'schema_version': 'session-bench-copilot-fresh-root-v1',
          'HOME': str(home), 'COPILOT_HOME': str(copilot_home), 'workspace': str(fixture),
          'home_inventory_before': inventory(home), 'copilot_home_inventory_before': inventory(copilot_home),
          'controller_sha256': state['controller_sha256'], 'credential_values_retained': False})
    write(run / 'capture-start.json', state)
    (run / 'capture').mkdir()
    try:
        token = subprocess.check_output(['gh', 'auth', 'token'], stderr=subprocess.DEVNULL, text=True, timeout=15).strip()
        if not token:
            raise ValueError('existing GitHub credential unavailable')
        env = {key: value for key, value in os.environ.items() if key in ('PATH', 'TMPDIR', 'LANG', 'TERM')}
        env.update(HOME=str(home), COPILOT_HOME=str(copilot_home), COPILOT_GITHUB_TOKEN=token, **canary_env)
        for index, turn in enumerate(workload['turns'], 1):
            args = [executable, '-p', turn['text'], '--model', 'auto', '--allow-all-tools',
                    '--output-format', 'json', '--no-custom-instructions', '--no-auto-update',
                    '--no-ask-user', '--no-bash-env', '--no-remote-export',
                    '--secret-env-vars=COPILOT_GITHUB_TOKEN',
                    '--session-id' if index == 1 else '--resume', session]
            write(run / f'capture/r{index}.launch.json', safe_launch_receipt(args, fixture=fixture, env=env, native_root=copilot_home, isolated_home=home))
            with (run / f'capture/r{index}.stdout').open('xb') as out, (run / f'capture/r{index}.stderr').open('xb') as err:
                process = subprocess.Popen(args, cwd=fixture, env=env, stdout=out, stderr=err, start_new_session=True)
                try:
                    code = process.wait(timeout=180)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGTERM)
                    try: process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid, signal.SIGKILL); process.wait()
                    raise ValueError('bounded turn timed out')
            write(run / f'capture/r{index}.exit.json', {'returncode': code, 'ended_ns': time.time_ns(),
                  'stdout_sha256': hashlib.sha256((run / f'capture/r{index}.stdout').read_bytes()).hexdigest(),
                  'stderr_sha256': hashlib.sha256((run / f'capture/r{index}.stderr').read_bytes()).hexdigest(),
                  'copilot_home_inventory_after': inventory(copilot_home)})
            state[f'r{index}_exit_code'] = code
            if code != 0:
                raise ValueError(f'R{index} failed; no retry')
            validate_stdout((run / f'capture/r{index}.stdout').read_bytes())
            ledger = [json.loads(line) for line in (fixture / '.survival-observer.jsonl').read_text().splitlines()]
            expected = [('inspect', 0), ('baseline', 1)] + ([('final', 0)] if index == 2 else [])
            if [(row['phase'], row['exit_code']) for row in ledger] != expected:
                raise ValueError('helper sequence incomplete; stop')
            if index == 1 and inventory(fixture)['checkout.py'] != initial['checkout.py']:
                raise ValueError('R1 modified checkout')
        state.update(status='completed', exit_code=0)
    except Exception as error:
        state.update(status='invalid_or_blocked', exit_code=1, error_type=type(error).__name__)
        if isinstance(error, ValueError): state['reason'] = str(error)
    finally:
        native = copilot_home / 'session-state' / session
        state['evidence_copy_verified'] = preserve(run, fixture, native)
        write(run / 'root-end.json', {'schema_version': 'session-bench-copilot-fresh-root-v1',
              'HOME': str(home), 'COPILOT_HOME': str(copilot_home), 'workspace': str(fixture),
              'home_inventory_after': inventory(home), 'copilot_home_inventory_after': inventory(copilot_home),
              'selected_session_id': session, 'selected_session_inventory': inventory(native) if native.exists() else {},
              'copied_session_inventory': inventory(run / 'native' / session) if (run / 'native' / session).exists() else {},
              'temporary_roots_retained': True, 'credential_values_retained': False})
        state['native_files'] = [p.relative_to(run).as_posix() for p in (run / 'native').rglob('*') if p.is_file()] if (run / 'native').exists() else []
        write(run / 'attempt.json', state)
    if state['status'] == 'completed':
        receipt = qualify_copilot_capture(run)
        write(run / 'qualification.json', receipt)
    print(json.dumps({'attempt': attempt, 'status': state['status'], 'evidence_copy_verified': state['evidence_copy_verified']}))
    return state['exit_code']


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--attempt-id', required=True)
    raise SystemExit(capture(parser.parse_args().attempt_id))
