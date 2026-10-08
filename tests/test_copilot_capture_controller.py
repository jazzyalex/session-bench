import importlib.util
import json
from pathlib import Path
import pytest

spec = importlib.util.spec_from_file_location('copilot_capture_controller', Path(__file__).resolve().parents[1] / 'scripts/run_copilot_survival.py')
controller = importlib.util.module_from_spec(spec)
spec.loader.exec_module(controller)


def test_preserves_helper_workspace_and_native_without_deleting_source(tmp_path):
    fixture = tmp_path / 'source'; fixture.mkdir()
    for name in ('checkout.py', 'bench_check.py', '.survival-observer.jsonl'):
        (fixture / name).write_text(name)
    native = tmp_path / 'session-id'; native.mkdir()
    (native / 'events.jsonl').write_text('{}\n')
    (native / 'companion.json').write_text('{}')
    run = tmp_path / 'run'; run.mkdir()
    assert controller.preserve(run, fixture, native)
    assert (fixture / '.survival-observer.jsonl').exists()
    assert (native / 'events.jsonl').exists()
    assert controller.inventory(run / 'workspace/fixture_project') == controller.inventory(fixture)
    assert (run / 'native/session-id/companion.json').exists()
    assert set(json.loads((run / 'filesystem/after-state.json').read_text())['files']) == set(controller.inventory(fixture))


def test_failed_attempt_without_native_still_preserves_observer(tmp_path):
    fixture = tmp_path / 'source'; fixture.mkdir()
    (fixture / '.survival-observer.jsonl').write_text('{}\n')
    run = tmp_path / 'run'; run.mkdir()
    assert controller.preserve(run, fixture, tmp_path / 'absent-session')
    assert (run / 'workspace/fixture_project/.survival-observer.jsonl').exists()


def test_rejects_symlinked_evidence(tmp_path):
    (tmp_path / 'link').symlink_to('/dev/null')
    with pytest.raises(ValueError, match='symlink'):
        controller.inventory(tmp_path)


def test_safe_launch_binds_fresh_roots_but_does_not_retain_token(tmp_path):
    home = tmp_path / 'home'; home.mkdir(); native = tmp_path / 'copilot'; native.mkdir()
    secret = 'private-credential-must-not-be-written'
    env = {'HOME': str(home), 'COPILOT_HOME': str(native), 'PATH': '/usr/bin', 'COPILOT_GITHUB_TOKEN': secret}
    receipt = controller.safe_launch_receipt(['copilot', '--model', 'auto'], fixture=tmp_path, env=env, native_root=native, isolated_home=home)
    assert secret not in json.dumps(receipt)
    assert 'COPILOT_GITHUB_TOKEN' not in receipt['safe_environment']
    assert receipt['secret_environment_variables'] == ['COPILOT_GITHUB_TOKEN']
    assert receipt['isolated_home_inventory_before'] == receipt['copilot_home_inventory_before'] == {}
    assert receipt['argv'] == ['copilot', '--model', 'auto']


def test_unexpected_inherited_auth_env_refused(tmp_path):
    with pytest.raises(ValueError, match='inherited environment'):
        controller.safe_launch_receipt(['copilot'], fixture=tmp_path, env={'COPILOT_GITHUB_TOKEN': 'x', 'OPENAI_API_KEY': 'y'}, native_root=tmp_path, isolated_home=tmp_path)


@pytest.mark.parametrize('rows', [
    [{'type': 'assistant.message', 'data': {'model': 'different'}}, {'type': 'result', 'exitCode': 0}],
    [{'type': 'assistant.message', 'data': {'model': 'gpt-6-luna'}}, {'type': 'result', 'exitCode': 1}],
    [{'type': 'assistant.message', 'data': {'model': 'gpt-6-luna'}}, {'type': 'error'}, {'type': 'result', 'exitCode': 0}],
])
def test_actual_model_change_runtime_error_and_failed_completion_stop(rows):
    with pytest.raises(ValueError): controller.validate_stdout(('\n'.join(json.dumps(row) for row in rows)).encode())


def test_expected_actual_model_and_successful_result_validate():
    rows = [{'type': 'assistant.message', 'data': {'model': 'gpt-6-luna'}}, {'type': 'result', 'exitCode': 0}]
    assert controller.validate_stdout(('\n'.join(json.dumps(row) for row in rows)).encode()) == rows
