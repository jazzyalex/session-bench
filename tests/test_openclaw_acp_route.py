"""OpenClaw route ``acp``: a fake gateway and a fake ACP bridge as real processes; no OpenClaw is started."""
import json
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import sys

import pytest

from session_bench import openclaw_state_capture as capture_module
from session_bench.openclaw_acp_observer import turn_summary
from session_bench.openclaw_state_capture import (
    GATEWAY_ENVIRONMENT, VERSION, OpenClawCaptureError, execute_openclaw_capture, port_open, prepare_openclaw_capture, redact,
    start_gateway, stop_gateway, verify_openclaw_state_capture,
)
from test_openclaw_state_capture import FIXED, OTHER, REPO, ROLLOUT, TID, _SETTLE, home, put, ready

FAKE = r'''#!__PYTHON__
"""A stand-in for the openclaw executable: version, config get, a foreground gateway, an ACP bridge."""
import json, os, signal, socket, sqlite3, subprocess, sys, time, uuid
from pathlib import Path
HOME, RECORD, PORT, TID, ROLLOUT = Path(__HOME__), Path(__RECORD__), __PORT__, __TID__, __ROLLOUT__
MODE = (RECORD / 'mode').read_text() if (RECORD / 'mode').exists() else 'normal'
args = sys.argv[1:]

def emit(message):
    sys.stdout.write(json.dumps({'jsonrpc': '2.0', **message}, ensure_ascii=False) + '\n')
    sys.stdout.flush()

def put(name, data):
    (HOME / name).parent.mkdir(parents=True, exist_ok=True)
    (HOME / name).write_bytes(data)

if args == ['--version']:
    print(__VERSION__)
elif args == ['gateway', 'run']:
    (RECORD / 'gateway-env.json').write_text(json.dumps({k: v for k, v in os.environ.items() if k.startswith('OPENCLAW_')}))
    if os.environ.get('OPENCLAW_SKIP_CHANNELS') != '1' or os.environ.get('OPENCLAW_SKIP_CRON') != '1':
        sys.exit(7)
    if MODE == 'gateway_exits':
        sys.exit(3)
    print('2026-10-07T12:47:10.698-07:00 [gateway] agent model: openai/gpt-5.6-terra (thinking=medium, fast=off)')
    print('gateway ready, token: ' + 'T0KENsecret' * 4, flush=True)
    server = socket.socket()
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(('127.0.0.1', PORT))
    server.listen()
    put('logs/gateway-2026-10-07.log', b'gateway log')
    put('gateway.pid', str(os.getpid()).encode())
    if MODE == 'stubborn':
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
    else:
        def leave(*_):
            (HOME / 'gateway.pid').unlink(missing_ok=True)
            sys.exit(0)
        signal.signal(signal.SIGTERM, leave)
    while True:
        time.sleep(0.05)
elif args[:1] == ['acp']:
    key, session, acp_session, turn = args[2], str(uuid.uuid4()), str(uuid.uuid4()), 0
    probe = socket.socket()
    if probe.connect_ex(('127.0.0.1', PORT)) != 0:
        sys.exit(5)
    probe.close()
    (RECORD / 'session.json').write_text(json.dumps({'session_id': session, 'acp_session_id': acp_session, 'key': key}))

    def update(body):
        emit({'method': 'session/update', 'params': {'sessionId': acp_session, 'update': body}})

    def shell(command, number):
        call = f'exec-{turn}-{number}'
        update({'sessionUpdate': 'tool_call', 'toolCallId': call, 'title': f"bash: command: /bin/zsh -lc '{command}', cwd: {os.getcwd()}",
                'status': 'in_progress', 'rawInput': {'command': f"/bin/zsh -lc '{command}'", 'cwd': os.getcwd()}, 'kind': 'execute'})
        code = subprocess.run(command, shell=True, capture_output=True).returncode
        update({'sessionUpdate': 'tool_call_update', 'toolCallId': call, 'status': 'completed',
                'rawOutput': {'status': 'completed', 'exitCode': code, 'durationMs': 3}})

    def rows(prompt):
        agent = sqlite3.connect(HOME / 'agents/main/agent/openclaw-agent.sqlite')
        threads = sqlite3.connect(HOME / 'agents/main/agent/codex-home/state_5.sqlite')
        history = sqlite3.connect(HOME / 'agents/main/agent/codex-home/thread_history_1.sqlite')
        if turn == 1:
            agent.execute('INSERT INTO session_nodes VALUES (?, ?, ?)', (key, session, '{}'))
            agent.execute('INSERT INTO session_windows VALUES (?, ?, NULL)', (session, key))
            threads.execute('INSERT INTO threads VALUES (?, ?, 0)', (TID, '/synthetic/rollout'))
        agent.execute('INSERT INTO transcript_events VALUES (?, ?, ?, ?)', (session, turn, json.dumps({'text': prompt}), b'z'))
        history.execute('INSERT INTO thread_turns VALUES (?, ?, ?)', (TID, f'turn-{turn}', 'completed'))
        # The gateway keeps replay rows under the session id of the ACP bridge, with the session key beside it.
        state = sqlite3.connect(HOME / 'state/openclaw.sqlite')
        state.execute('CREATE TABLE IF NOT EXISTS acp_replay_sessions (session_id TEXT PRIMARY KEY, session_key TEXT, cwd TEXT)')
        state.execute('CREATE TABLE IF NOT EXISTS acp_replay_events (session_id TEXT REFERENCES acp_replay_sessions(session_id), seq INTEGER, payload TEXT)')
        if turn == 1:
            state.execute('INSERT INTO acp_replay_sessions VALUES (?, ?, ?)', (acp_session, key, os.getcwd()))
            state.execute("INSERT INTO acp_replay_sessions VALUES ('7085af1a-0000-4000-8000-00000000beef', 'agent:main:main', '/other')")
            state.execute("INSERT INTO acp_replay_events VALUES ('7085af1a-0000-4000-8000-00000000beef', 1, 'other private replay')")
        state.execute('INSERT INTO acp_replay_events VALUES (?, ?, ?)', (acp_session, turn, 'replay of turn %d' % turn))
        for connection in (agent, threads, history, state):
            connection.commit()
            connection.close()
        put(ROLLOUT, b'{"type":"session_meta"}\n' + b'{"type":"turn"}\n' * turn)
        put('agents/main/sessions/sessions.json', b'{"changed": %d}' % turn)
        if MODE == 'unknown_file':
            put('devices/unknown.bin', b'?')

    for line in sys.stdin:
        message = json.loads(line)
        method, identity = message.get('method'), message.get('id')
        if method == 'initialize':
            emit({'id': identity, 'result': {'protocolVersion': 1, 'agentInfo': {'name': 'openclaw-acp', 'version': '2026.9.8'}}})
        elif method == 'session/new':
            emit({'id': identity, 'result': {'sessionId': acp_session, 'configOptions': []}})
            update({'sessionUpdate': 'session_info_update', 'title': key, '_meta': {'sessionKey': key if MODE != 'other_key' else 'agent:main:main'}})
            update({'sessionUpdate': 'available_commands_update', 'availableCommands': []})
        elif method == 'session/prompt':
            turn += 1
            prompt = message['params']['prompt'][0]['text']
            canary = os.environ['SB_SURVIVAL_V1_RUN_CANARY']
            if MODE == 'acp_dies':
                sys.exit(4)
            if turn == 1:
                shell(f'cd fixture_project && python3 bench_check.py inspect --run-canary {canary}', 1)
                shell(f'cd fixture_project && python3 bench_check.py baseline --run-canary {canary}', 2)
                reply = ['Baseline: 2 of 3 fail. ', 'SB_SURVIVAL_V1_RESPONSE_R1_cafe_🙂']
            else:
                emit({'id': 900, 'method': 'session/request_permission', 'params': {'sessionId': acp_session, 'toolCall': {'toolCallId': 'patch-2-1'},
                      'options': [{'optionId': 'no', 'kind': 'reject_once', 'name': 'Reject'}, {'optionId': 'yes', 'kind': 'allow_once', 'name': 'Allow'}]}})
                answer = json.loads(sys.stdin.readline())
                assert answer['id'] == 900 and answer['result']['outcome'] == {'outcome': 'selected', 'optionId': 'yes'}, answer
                update({'sessionUpdate': 'tool_call', 'toolCallId': 'patch-2-1', 'title': 'apply_patch', 'status': 'in_progress', 'kind': 'edit',
                        'rawInput': {'patch': '*** Begin Patch\n*** Update File: fixture_project/checkout.py\n*** End Patch'}})
                Path('fixture_project/checkout.py').write_text(__FIXED__)
                update({'sessionUpdate': 'tool_call_update', 'toolCallId': 'patch-2-1', 'status': 'completed', 'rawOutput': {'status': 'completed'}})
                shell(f'cd fixture_project && python3 bench_check.py final --run-canary {canary}', 2)
                reply = ['Fixed. ', 'SB_SURVIVAL_V1_RESPONSE_R2_correction_Δ']
            rows(prompt)
            for part in reply:
                update({'sessionUpdate': 'agent_message_chunk', 'content': {'type': 'text', 'text': part}})
            update({'sessionUpdate': 'usage_update', 'used': 27000 + turn, 'size': 258400, '_meta': {'source': 'gateway-session-store', 'approximate': True}})
            emit({'id': identity, 'result': {'stopReason': 'end_turn' if MODE != 'no_end' else 'refusal'}})
else:
    sys.exit(9)
'''


_OPEN = []


def free_port():
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        return probe.getsockname()[1]


def prepared(tmp_path, monkeypatch, mode='normal'):
    for name in [key for key in os.environ if key.startswith(('OPENCLAW_', 'CLAWDBOT_'))]:
        monkeypatch.delenv(name)
    repository = tmp_path / 'repo'
    workload_root = repository / 'fixtures/scenarios/survival-v1/workload'
    workload_root.parent.mkdir(parents=True)
    shutil.copytree(REPO / 'fixtures/scenarios/survival-v1/workload', workload_root)
    destination = repository / 'artifacts/v1-expanded-preparation/live-captures/openclaw-acp-test'
    destination.parent.mkdir(parents=True)
    root, stores = home(tmp_path)
    _OPEN.append(stores)   # the writer connections stay open: closing one would remove its -wal and -shm files during a test
    workspace = tmp_path / 'owner-workspace'
    put(workspace, 'AGENTS.md', b'owner instructions')
    record, port = tmp_path / 'record', free_port()
    record.mkdir()
    (record / 'mode').write_text(mode)
    executable = tmp_path / 'openclaw'
    text = FAKE
    for mark, value in (('__PYTHON__', sys.executable), ('__HOME__', repr(str(root))), ('__RECORD__', repr(str(record))), ('__PORT__', str(port)),
                        ('__TID__', repr(TID)), ('__ROLLOUT__', repr(ROLLOUT)), ('__VERSION__', repr(VERSION)), ('__FIXED__', repr(FIXED))):
        text = text.replace(mark, value)
    executable.write_text(text)
    executable.chmod(0o755)
    plan = prepare_openclaw_capture(destination, repository=repository, repetition=1, executable=executable, openclaw_home=root,
                                    route='acp', gateway_port=port)
    return destination, plan, root, workspace, record, stores


def run(tmp_path, monkeypatch, mode='normal'):
    destination, plan, root, workspace, record, _stores = prepared(tmp_path, monkeypatch, mode)
    result = execute_openclaw_capture(destination, preflight=ready(workspace), state_settle=_SETTLE)
    return destination, plan, root, workspace, record, result


def assert_all_clean(plan, workspace, result):
    assert result['fixture_removed'] is True and not os.path.lexists(workspace / 'fixture_project')
    assert result['gateway_stopped'] is True and 'GATEWAY_NOT_STOPPED' not in result and not port_open(plan['gateway_port'])


def test_plan_of_the_acp_route_names_the_gateway_the_bridge_and_the_switches(tmp_path, monkeypatch):
    destination, plan, _root, workspace, _record, _stores = prepared(tmp_path, monkeypatch)
    executable = plan['executable']
    assert plan['route'] == 'acp' and plan['session_id'] is None and plan['argv'] is None
    assert plan['session_key'] == 'agent:main:explicit:' + plan['key_id'] and len(plan['key_id']) == 36
    assert plan['gateway_argv'] == [executable, 'gateway', 'run']
    assert plan['acp_argv'] == [executable, 'acp', '--session', plan['session_key'], '--no-prefix-cwd']
    assert plan['gateway_environment'] == GATEWAY_ENVIRONMENT and GATEWAY_ENVIRONMENT['OPENCLAW_SKIP_CHANNELS'] == GATEWAY_ENVIRONMENT['OPENCLAW_SKIP_CRON'] == '1'
    assert set(GATEWAY_ENVIRONMENT) >= {'OPENCLAW_SKIP_GMAIL_WATCHER', 'OPENCLAW_SKIP_BROWSER_CONTROL_SERVER', 'OPENCLAW_SKIP_CANVAS_HOST',
                                       'OPENCLAW_DISABLE_BONJOUR', 'OPENCLAW_NO_AUTO_UPDATE'}
    flat = plan['gateway_argv'] + plan['acp_argv']
    assert not {'install', 'start', 'restart', '--model', '--force', '--token', '--password'} & set(flat)
    assert not os.path.lexists(workspace / 'fixture_project') and not port_open(plan['gateway_port'])
    with pytest.raises(ValueError, match='route must be'):
        prepare_openclaw_capture(destination.with_name('openclaw-x'), repository=tmp_path / 'repo', repetition=1, executable=executable, route='tui')


def test_acp_capture_runs_both_turns_in_one_process_stops_the_gateway_and_brackets_the_home_once(tmp_path, monkeypatch):
    destination, plan, root, workspace, record, result = run(tmp_path, monkeypatch)
    assert result['status'] == 'captured_pending_qualification', result
    assert_all_clean(plan, workspace, result)
    fake = json.loads((record / 'session.json').read_text())
    # The session id was not known before the run: the row of the session key in the agent store gives it.
    assert result['route'] == 'acp' and result['model_submissions'] == 2 and result['session_key'] == plan['session_key'] == fake['key']
    assert result['session_id'] == fake['session_id'] != plan['key_id'] and result['thread_id'] == TID
    assert result['acp_session_id'] == fake['acp_session_id'] and result['acp_returncode'] == 0
    # The gateway ran with channels and timed jobs switched off, in the foreground.
    environment = json.loads((record / 'gateway-env.json').read_text())
    assert environment == GATEWAY_ENVIRONMENT
    launch = json.loads((destination / 'gateway-launch.json').read_text())
    assert launch['argv'] == plan['gateway_argv'] and launch['foreground'] is True and launch['service_installed'] is False
    stop = json.loads((destination / 'gateway-stop.json').read_text())
    assert stop['stopped'] is True and stop['port_closed'] is True and stop['signal'] == 'SIGTERM' and stop['returncode'] == 0
    # The gateway output is kept only cut: no token-like run of characters.
    kept = (destination / 'gateway-stdout.redacted.txt').read_text()
    assert 'gateway ready, token: <cut>' in kept and 'T0KENsecret' not in kept
    assert not list(Path(os.environ.get('TMPDIR', '/tmp')).glob(f"session-bench-openclaw-gateway-*{launch['pid']}*"))
    for turn in (1, 2):
        prefix = destination / f'turn-r{turn}'
        summary = turn_summary((prefix / 'acp-stream.jsonl').read_bytes())
        assert summary['acp_session_id'] == fake['acp_session_id'] and summary['stop_reason'] == 'end_turn'
        assert summary['prompt'] == (destination / f'observer/prompt-r{turn}.txt').read_text()
        receipt = json.loads((prefix / 'acp-receipt.json').read_text())
        assert receipt['tool_events_in_stream'] is True and receipt['tool_calls'] == 2 == receipt['tool_calls_with_arguments']
        assert receipt['permission_requests_answered'] == (0 if turn == 1 else 1)
        assert (prefix / 'workspace/fixture_project/.survival-observer.jsonl').is_file()
        assert json.loads((prefix / 'launch.json').read_text())['model_override'] is None
    first = turn_summary((destination / 'turn-r1/acp-stream.jsonl').read_bytes())
    assert first['session_keys'] == [plan['session_key']] and [call['raw_output']['exitCode'] for call in first['calls']] == [0, 1]
    second = turn_summary((destination / 'turn-r2/acp-stream.jsonl').read_bytes())
    assert [call['title'].split(':')[0] for call in second['calls']] == ['apply_patch', 'bash']
    assert second['permissions'][0]['answer'] == 'yes' and second['permissions'][0]['answer_kind'] == 'allow_once'
    assert second['text'].endswith('SB_SURVIVAL_V1_RESPONSE_R2_correction_Δ')
    # One native receipt, after both turns and after the gateway stopped.
    assert not (destination / 'r1-native-receipt.json').exists()
    receipt = json.loads((destination / 'r2-native-receipt.json').read_text())
    before = json.loads((destination / 'state-before.json').read_text())
    after = json.loads((destination / 'r2-state-after.json').read_text())
    assert verify_openclaw_state_capture(receipt, destination / 'r2-native', before=before, after=after) is True
    assert receipt['session_id'] == fake['session_id'] and receipt['key_id'] == plan['key_id'] and receipt['thread_id'] == TID
    assert receipt['session_id_source'].startswith('row of the session key in agents/main/agent/openclaw-agent.sqlite')
    assert receipt['thread_id_source'] == 'name of the one new rollout file'
    # What the gateway itself wrote is classified: a new log is run-owned; its pid file came and went.
    assert 'logs/gateway-2026-10-07.log' in receipt['classes']['run_owned'] and ROLLOUT in receipt['classes']['session_owned']
    rows = (destination / 'r2-native/session-store-rows.json').read_text()
    assert OTHER not in rows and 'other private' not in rows and 'OWNER-' not in rows
    # Rows that the gateway keeps under the ACP session id are rows of the test session; another bridge session is not.
    replay = {table['table']: table['rows'] for table in json.loads(rows)['stores'][-1]['tables']}
    assert [row['values']['session_id'] for row in replay['acp_replay_events']] == [fake['acp_session_id']] * 2
    assert len(replay['acp_replay_sessions']) == 1 and 'other private replay' not in rows and receipt['acp_session_id'] == fake['acp_session_id']
    assert (result['provider'], result['model']) == ('openai', 'gpt-5.6-terra') and stop['agent_model']['model'] == 'gpt-5.6-terra'
    events = next(table for table in json.loads(rows)['stores'][0]['tables'] if table['table'] == 'transcript_events')
    assert len(events['rows']) == 2 and not (destination / '.r2-native-store-copy').exists()
    assert (destination / 'workspaces/after/fixture_project/checkout.py').read_text() == FIXED


@pytest.mark.parametrize('mode, reason, submissions', [
    ('gateway_exits', 'the gateway exited with code 3 before its port opened', 0),
    ('acp_dies', 'closed its output before the response of session/prompt', 1),
    ('no_end', 'did not end normally', 1),
    ('other_key', 'names another session key', 1),
    ('unknown_file', 'native state capture refused', 2),
])
def test_acp_failure_stops_the_gateway_removes_the_fixture_and_retries_nothing(tmp_path, monkeypatch, mode, reason, submissions):
    destination, plan, _root, workspace, _record, result = run(tmp_path, monkeypatch, mode)
    assert result['status'] == 'capture_incomplete' and reason in result['failure'], result
    assert result['model_submissions'] == submissions
    assert_all_clean(plan, workspace, result)
    assert (destination / 'gateway-stop.json').is_file() and (destination / 'workspaces/after/fixture_project/bench_check.py').is_file()
    if mode == 'unknown_file':
        refusal = json.loads((destination / 'r2-refusal.json').read_text())
        assert [row['relative_path'] for row in refusal['unexplained']] == ['devices', 'devices/unknown.bin']


def test_a_gateway_that_ignores_the_stop_signal_is_killed_and_the_port_is_checked(tmp_path, monkeypatch):
    destination, plan, _root, workspace, _record, result = run(tmp_path, monkeypatch, 'stubborn')
    stop = json.loads((destination / 'gateway-stop.json').read_text())
    assert stop['signal'] == 'SIGKILL' and stop['stopped'] is True and stop['port_closed'] is True
    assert_all_clean(plan, workspace, result)


def test_an_open_gateway_port_is_refused_before_anything_is_placed_or_started(tmp_path, monkeypatch):
    destination, plan, _root, workspace, record, _stores = prepared(tmp_path, monkeypatch)
    with socket.socket() as other:
        other.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        other.bind(('127.0.0.1', plan['gateway_port']))
        other.listen()
        result = execute_openclaw_capture(destination, preflight=ready(workspace), state_settle=_SETTLE)
        assert result['status'] == 'capture_incomplete' and 'is already open: another gateway is running' in result['failure']
        assert result['model_submissions'] == 0 and result['fixture_placed'] is False and result['gateway_started'] is False
        assert not (record / 'gateway-env.json').exists() and not os.path.lexists(workspace / 'fixture_project')
        # The guard of the start function itself: it starts nothing on an open port.
        with pytest.raises(OpenClawCaptureError, match='already open'):
            start_gateway(plan, {'PATH': os.environ['PATH']}, workspace, {})
        assert port_open(plan['gateway_port'])    # the other listener was not touched


def test_gateway_is_never_started_without_the_channel_and_cron_switches(tmp_path, monkeypatch):
    _destination, plan, _root, workspace, record, _stores = prepared(tmp_path, monkeypatch)
    for broken in ({**plan, 'gateway_environment': {**GATEWAY_ENVIRONMENT, 'OPENCLAW_SKIP_CHANNELS': '0'}},
                   {**plan, 'gateway_environment': {key: value for key, value in GATEWAY_ENVIRONMENT.items() if key != 'OPENCLAW_SKIP_CRON'}},
                   {**plan, 'gateway_argv': [plan['executable'], 'gateway', 'install']},
                   {**plan, 'gateway_argv': [plan['executable'], 'gateway', 'restart']}):
        with pytest.raises(OpenClawCaptureError, match='nothing was started'):
            start_gateway(broken, {'PATH': os.environ['PATH']}, workspace, {})
    assert not (record / 'gateway-env.json').exists()


def test_gateway_is_stopped_on_an_interrupt(tmp_path, monkeypatch):
    destination, plan, _root, workspace, _record, _stores = prepared(tmp_path, monkeypatch)
    real = capture_module.AcpClient.request

    def interrupted(self, method, params, timeout):
        if method == 'session/prompt':
            raise KeyboardInterrupt
        return real(self, method, params, timeout)

    monkeypatch.setattr(capture_module.AcpClient, 'request', interrupted)
    with pytest.raises(KeyboardInterrupt):
        execute_openclaw_capture(destination, preflight=ready(workspace), state_settle=_SETTLE)
    result = json.loads((destination / 'capture-result.json').read_text())
    assert result['failure'] == 'interrupted: KeyboardInterrupt' and result['gateway_started'] is True
    assert_all_clean(plan, workspace, result)


def test_start_and_stop_guard_alone(tmp_path, monkeypatch):
    destination, plan, _root, workspace, _record, _stores = prepared(tmp_path, monkeypatch)
    record = {}
    start_gateway(plan, {'PATH': os.environ['PATH']}, workspace, record)
    assert port_open(plan['gateway_port']) and record['pid'] == record['process'].pid and record['stopped'] is False
    stop = stop_gateway(record, destination)
    assert stop['stopped'] is True and not port_open(plan['gateway_port']) and record['stopped'] is True
    assert not Path(record['stdout']).exists() and not Path(record['stderr']).exists()


def test_redaction_cuts_token_like_text_and_the_home_path():
    home_path = str(Path.home())
    text = redact(f'listening on ws://127.0.0.1:18789/?token=abcDEF0123456789abcDEF0123456789 in {home_path}/x\nshort ok\n'.encode()).decode()
    assert 'abcDEF0123456789' not in text and home_path not in text and 'short ok' in text and '<cut>' in text and '~/x' in text


def test_session_id_lookup_needs_exactly_one_row_for_a_key_that_the_controller_made(tmp_path):
    from session_bench.openclaw_state_capture import OpenClawStateError, RULES, _discover_session_id, inventory_openclaw_home, rules_table
    root, stores = home(tmp_path)
    key_id = '0a0a0a0a-1111-4222-8333-444444444444'
    key = 'agent:main:explicit:' + key_id
    entries = lambda: {row['relative_path']: row for row in inventory_openclaw_home(root)['entries']}  # noqa: E731
    rules = rules_table(RULES)
    with pytest.raises(OpenClawStateError, match='holds 0 session ids'):
        _discover_session_id(root, entries(), key, key_id, rules, tmp_path / '.copy')
    agent = stores['agents/main/agent/openclaw-agent.sqlite']
    agent.execute('INSERT INTO session_nodes VALUES (?, ?, ?)', (key, '9f1c2d3e-0abd-4117-af54-3aade9c80077', '{}'))
    agent.commit()
    assert _discover_session_id(root, entries(), key, key_id, rules, tmp_path / '.copy') == '9f1c2d3e-0abd-4117-af54-3aade9c80077'
    assert not (tmp_path / '.copy').exists()
    # A shared key, or a key without the controller's id, is never looked up.
    for bad_key, bad_id in (('agent:main:main', key_id), (key, None), ('agent:main:explicit:' + OTHER, key_id)):
        with pytest.raises(OpenClawStateError, match='not one that the controller made'):
            _discover_session_id(root, entries(), bad_key, bad_id, rules, tmp_path / '.copy')
    assert isinstance(stores, dict) and sqlite3.sqlite_version


def test_a_refused_bracket_can_be_taken_again_with_an_extended_rule_table_when_the_home_did_not_change(tmp_path, monkeypatch):
    from session_bench.openclaw_state_capture import RULES, rebracket_openclaw_capture
    destination, plan, root, workspace, record, result = run(tmp_path, monkeypatch, 'unknown_file')
    assert 'native state capture refused' in result['failure'] and result['model_submissions'] == 2
    # With the same rule table the refusal repeats, and nothing is written as a receipt.
    again = rebracket_openclaw_capture(destination, state_settle=_SETTLE)
    assert again['status'] == 'capture_incomplete' and 'refused again' in again['failure'] and not (destination / 'r2-native-receipt.json').exists()
    monkeypatch.setitem(capture_module.RULES, 'shared_transient', RULES['shared_transient'] + (r'devices/unknown\.bin',))
    done = rebracket_openclaw_capture(destination, state_settle=_SETTLE)
    assert done['status'] == 'captured_pending_qualification' and done['model_submissions_in_rebracket'] == 0
    assert done['rules_added_after_refusal'] == {'shared_transient': ['devices/unknown\\.bin']} and done['home_unchanged_since_refusal'] is True
    assert done['session_id'] == json.loads((record / 'session.json').read_text())['session_id'] and done['thread_id'] == TID
    receipt = json.loads((destination / 'r2-native-receipt.json').read_text())
    before = json.loads((destination / 'state-before.json').read_text())
    after = json.loads((destination / 'r2-state-after.json').read_text())
    assert verify_openclaw_state_capture(receipt, destination / 'r2-native', before=before, after=after) is True
    # The first result and the refusal stay as they were.
    assert json.loads((destination / 'capture-result.json').read_text())['status'] == 'capture_incomplete' and (destination / 'r2-refusal.json').is_file()
    with pytest.raises(OpenClawCaptureError, match='not a complete ACP run'):
        rebracket_openclaw_capture(destination, state_settle=_SETTLE)


def test_rebracket_is_refused_when_the_home_changed_since_the_refusal(tmp_path, monkeypatch):
    from session_bench.openclaw_state_capture import rebracket_openclaw_capture
    destination, _plan, root, _workspace, _record, _result = run(tmp_path, monkeypatch, 'unknown_file')
    put(root, 'logs/later.log', b'something ran after the refusal')
    with pytest.raises(OpenClawCaptureError, match='changed since the refusal'):
        rebracket_openclaw_capture(destination, state_settle=_SETTLE)
