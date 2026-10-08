"""Kimi observer from the stream-json stdout: what the stream holds, and the join to the native decode."""
import json

import pytest

from session_bench.kimi_stream_observer import (
    KimiStreamError, build_kimi_stream_observer, parse_stream, stream_exit_code, stream_summary,
)
from session_bench.kimi_wire_records import add_native_final_after_chain
from session_bench.live_metric_comparator import compare_survival_run
from test_kimi_score_replay import (
    AFTER, ALIAS, BEFORE, CANARY, PORTABLE, PROMPTS, R1, R2, SID, Session, compact, edit_call, helper, helper_line, normal_session, read_call, sha, shell,
)

WORKLOAD = {'run_id': 'kimi-test', 'run_canary': CANARY,
            'turns': [{'id': 'turn-r1', 'revision': 'r1', 'text': PROMPTS[0], 'response_canary': R1},
                      {'id': 'turn-r2', 'revision': 'r2', 'text': PROMPTS[1], 'response_canary': R2}]}
MODELS = [{'model_id': 'kimi-k2.7-code', 'configuration': {'model_alias': ALIAS}}] * 2


def ledger(codes=(0, 1, 0)):
    return ''.join(compact({'phase': phase, 'helper_nonce': phase + '-fixture-0001', 'argv': ['python3', 'bench_check.py', phase],
                            'exit_code': code, 'output': helper_line(phase)}) + '\n' for phase, code in zip(('inspect', 'baseline', 'final'), codes)).encode()


def observe(session, **changes):
    options = dict(workload=WORKLOAD, stdout_by_turn={1: session.stdout(1), 2: session.stdout(2)}, helper_document=ledger(),
                   before_sha256=sha(BEFORE.encode()), after_sha256=sha(AFTER.encode()), models=MODELS)
    options.update(changes)
    return build_kimi_stream_observer(**options)


def events(observer, kind, role='primary_scored'):
    return [row for row in observer['events'] if row['kind'] == kind and row['population_role'] == role]


def test_stream_gives_each_call_with_id_arguments_and_result_and_the_final_text():
    session = normal_session(retry=True)
    summary = stream_summary(session.stdout(2), 'stdout R2')
    assert (summary['version'], summary['session_id'], summary['retries']) == ('2.1.1', SID, 2)
    assert [(call['id'], call['name']) for call in summary['calls']] == [('Read_2_0000beef', 'Read'), ('Edit_3_0000beef', 'Edit'), ('Bash_4_0000beef', 'Bash')]
    assert summary['calls'][2]['arguments']['command'] == helper('final') and summary['calls'][2]['content'] == helper_line('final') + '\n'
    assert summary['text'].endswith(R2)
    # The stream has no status field: a failure is the exit code line at the end of the result text.
    baseline = stream_summary(session.stdout(1))['calls'][1]
    assert set(json.loads(session.stdout(1).splitlines()[4])) == {'role', 'tool_call_id', 'content'}
    assert stream_exit_code(baseline['content']) == 1 and stream_exit_code(summary['calls'][2]['content']) == 0


def test_observer_has_four_scored_actions_with_native_call_ids_and_the_ledger_exit_codes():
    observer = observe(normal_session())
    actions = events(observer, 'action')
    assert [(row['id'], row['fields']['action_kind'], row['fields']['call_id']) for row in actions] == [
        ('action-Bash_0_0000beef', 'inspect', 'Bash_0_0000beef'), ('action-Bash_1_0000beef', 'test', 'Bash_1_0000beef'),
        ('action-Edit_3_0000beef', 'edit', 'Edit_3_0000beef'), ('action-Bash_4_0000beef', 'test', 'Bash_4_0000beef')]
    results = {row['fields']['action_id']: row['fields'] for row in events(observer, 'result')}
    assert (results['action-Bash_1_0000beef']['exit_code'], results['action-Bash_1_0000beef']['status'], results['action-Bash_1_0000beef']['output']) == (
        1, 'failure', helper_line('baseline'))
    assert results['action-Edit_3_0000beef']['status'] == 'success' and 'exit_code' not in results['action-Edit_3_0000beef']
    # The read is observed and unscored.
    assert [row['fields']['name'] for row in events(observer, 'action', 'unscored')] == ['Read']
    responses = events(observer, 'assistant_response')
    assert [row['fields']['canary'] for row in responses] == [R1, R2] and responses[0]['fields']['model_id'] == 'kimi-k2.7-code'
    assert {row['session_id'] for row in observer['events']} == {SID}
    assert sorted(row['kind'] for row in observer['relations']) == ['action_result'] * 4 + ['final_after', 'helper_for', 'helper_for', 'helper_for', 'supersedes',
                                                                                             'turn_response', 'turn_response']


@pytest.mark.parametrize('retry,compound', [(False, False), (True, False), (False, True)])
def test_observer_and_native_decode_agree_on_every_survival_metric(retry, compound):
    session = normal_session(retry=retry, compound=compound)
    observer, native = observe(session), session.decode()
    add_native_final_after_chain(native, WORKLOAD)
    rows = {row['id']: row for row in compare_survival_run(observer, native, PORTABLE, configuration_id='kimi', repetition=1)['metrics']}
    # No record declares a usage total: with a complete root that is a proven absence.
    assert rows.pop('attribution.reconciliation')['state'] == 'native_absent'
    if compound:
        # The first helper of a compound call has no exit code of its own in the native result (no echo, no && chain):
        # its result earns no credit, and so does the relation to it.
        for name in ('work.results', 'causal.action_result'):
            assert (rows[name]['correct'], rows[name]['observed_eligible']) == (3, 4)
            del rows[name]
    assert {row['state'] for row in rows.values()} == {'measured'} and all(row['correct'] == row['observed_eligible'] for row in rows.values())
    assert rows['work.actions']['decoded_eligible'] == 4


def test_compound_call_is_observed_as_one_action_per_helper_segment():
    observer = observe(normal_session(compound=True))
    parts = [row for row in events(observer, 'action') if 'segment_index' in row['fields']]
    assert [(row['id'], row['fields']['argv'][2], row['fields']['call_id']) for row in parts] == [
        ('action-Bash_0_0000beef:segment-0', 'inspect', 'Bash_0_0000beef'), ('action-Bash_0_0000beef:segment-1', 'baseline', 'Bash_0_0000beef')]
    results = {row['fields']['action_id']: row['fields'] for row in events(observer, 'result')}
    assert [results[row['id']]['exit_code'] for row in parts] == [0, 1]


def test_a_stream_that_is_not_one_complete_turn_fails_closed():
    session = normal_session()
    lines = session.stdout(2).splitlines(keepends=True)
    with pytest.raises(KimiStreamError, match='resume hint'):
        stream_summary(b''.join(lines[:-1]))
    with pytest.raises(KimiStreamError, match='without a result'):
        stream_summary(b''.join(line for line in lines if b'"tool_call_id":"Bash_4' not in line))
    with pytest.raises(KimiStreamError, match='not a stream-json line'):
        parse_stream(b'{"role":"user","content":"x"}\n')
    with pytest.raises(KimiStreamError, match='not JSON'):
        parse_stream(b'{"role"\n')
    with pytest.raises(KimiStreamError, match='final assistant text'):
        stream_summary(b''.join(line for line in lines if R2.encode() not in line))


def test_a_stream_that_differs_from_the_ledger_or_lacks_a_scored_action_fails_closed():
    session = normal_session()
    with pytest.raises(KimiStreamError, match='differs from the helper ledger'):
        observe(session, helper_document=ledger((0, 0, 0)))   # the stream states exit code 1 for the baseline
    with pytest.raises(KimiStreamError, match='helper population incomplete'):
        observe(session, helper_document=ledger()[:-10].rsplit(b'\n', 1)[0] + b'\n')
    without_edit = Session().prompt(PROMPTS[0]).round([shell(helper('inspect'), helper_line('inspect'))]).round(
        [shell(helper('baseline'), helper_line('baseline'), 1)]).round(text=R1).end().prompt(PROMPTS[1]).round(
        [shell(helper('final'), helper_line('final'))]).round(text=R2).end()
    with pytest.raises(KimiStreamError, match='no observed edit'):
        observe(without_edit)
    with pytest.raises(KimiStreamError, match='lacks the exact canary'):
        observe(session, workload={**WORKLOAD, 'turns': [WORKLOAD['turns'][0], {**WORKLOAD['turns'][1], 'response_canary': 'SB_OTHER'}]})
    two_edits = Session().prompt(PROMPTS[0]).round(text=R1).end().prompt(PROMPTS[1]).round([read_call(), edit_call()]).round([edit_call()]).round(text=R2).end()
    with pytest.raises(KimiStreamError, match='no rule'):
        observe(two_edits)
    with pytest.raises(KimiStreamError, match='did not change'):
        observe(session, after_sha256=sha(BEFORE.encode()))


def test_observed_edit_arguments_equal_the_native_ones_and_a_changed_argument_fails_the_check():
    import copy
    from session_bench.kimi_score_inputs import check_observed_edit_arguments
    session = normal_session()
    observer, native = observe(session), session.decode()
    edit, = [row for row in events(observer, 'action') if row['fields']['action_kind'] == 'edit']
    # The observer keeps the arguments of the edit; the native record of the same call has the same ones.
    assert edit['fields']['input'] and edit['fields']['input'] == next(row for row in native['actions'] if row['call_id'] == edit['fields']['call_id'])['input']
    document = json.dumps(observer).encode()
    assert check_observed_edit_arguments(document, native) == 1
    # Negative controls: one changed argument on either side, or an observed edit that the native record lacks.
    for key, value in edit['fields']['input'].items():
        if isinstance(value, str):
            changed = copy.deepcopy(observer)
            next(row for row in changed['events'] if row['id'] == edit['id'])['fields']['input'][key] = value + ' '
            with pytest.raises(ValueError, match='differ from the native record'):
                check_observed_edit_arguments(json.dumps(changed).encode(), native)
            damaged = copy.deepcopy(native)
            next(row for row in damaged['actions'] if row['call_id'] == edit['fields']['call_id'])['input'][key] = value + ' '
            with pytest.raises(ValueError, match='differ from the native record'):
                check_observed_edit_arguments(document, damaged)
    missing = copy.deepcopy(native)
    missing['actions'] = [row for row in missing['actions'] if row['call_id'] != edit['fields']['call_id']]
    with pytest.raises(ValueError, match='no native record'):
        check_observed_edit_arguments(document, missing)
    # An observed edit without its arguments fails (it was skipped before); so does an empty one.
    for arguments in ('absent', {}, None, 'text'):
        without = copy.deepcopy(observer)
        fields = next(row for row in without['events'] if row['id'] == edit['id'])['fields']
        if arguments == 'absent':
            del fields['input']
        else:
            fields['input'] = arguments
        with pytest.raises(ValueError, match='no arguments'):
            check_observed_edit_arguments(json.dumps(without).encode(), native)
    # The comparator does not compare edit text: the changed observer still compares equal there. The check above is the Kimi-side control.
    changed = copy.deepcopy(observer)
    next(row for row in changed['events'] if row['id'] == edit['id'])['fields']['input']['new_string'] = 'other'
    add_native_final_after_chain(native, WORKLOAD)
    rows = {row['id']: row for row in compare_survival_run(changed, native, PORTABLE, configuration_id='kimi', repetition=1)['metrics']}
    assert rows['work.actions']['correct'] == rows['work.actions']['observed_eligible']


def test_the_replay_fails_when_no_edit_was_compared_although_the_workload_has_a_scored_edit(monkeypatch):
    from session_bench import kimi_score_inputs
    session = normal_session()
    native = session.decode()
    monkeypatch.setattr(kimi_score_inputs, 'decode_kimi_native', lambda directory: native)
    # The observer has no edit event: the check compares none.
    without_edit = json.loads(json.dumps(observe(session)))
    without_edit['events'] = [row for row in without_edit['events'] if row.get('fields', {}).get('action_kind') != 'edit']
    assert kimi_score_inputs.check_observed_edit_arguments(json.dumps(without_edit).encode(), native) == 0
    with pytest.raises(ValueError, match='compared no edit although the workload has a scored edit'):
        kimi_score_inputs.build_kimi_replay_evidence('root', 'native', {'actions': [{'kind': 'edit'}]}, {}, {'observer_document': json.dumps(without_edit).encode()})
    # A workload without a scored edit does not need one; the build then goes on (here it stops at the next step).
    monkeypatch.setattr(kimi_score_inputs, 'add_native_final_after_chain', lambda *_: (_ for _ in ()).throw(RuntimeError('went on')))
    with pytest.raises(RuntimeError, match='went on'):
        kimi_score_inputs.build_kimi_replay_evidence('root', 'native', {'actions': [{'kind': 'test'}]}, {}, {'observer_document': json.dumps(without_edit).encode()})
    with pytest.raises(RuntimeError, match='went on'):
        kimi_score_inputs.build_kimi_replay_evidence('root', 'native', {'actions': [{'kind': 'edit'}]}, {}, {'observer_document': json.dumps(observe(session)).encode()})
