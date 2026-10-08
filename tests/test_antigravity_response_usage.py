"""Final usage is bound to one displayed response, never aggregate usage."""
import json
from pathlib import Path

import pytest

from session_bench.antigravity_response_usage import project_final_response_usage


def stream(*, response='hello world', done_usage=True):
    rows = [
        {'event': 'init', 'conversation_id': 'session'},
        {'step_update': {'conversation_id': 'session', 'step_index': 1, 'step_type': 'agent_response',
                         'state': 'DONE', 'usage': {'input_tokens': 100, 'output_tokens': 20}}},
        {'step_update': {'conversation_id': 'session', 'step_index': 2, 'step_type': 'agent_response', 'text_delta': 'hello '}},
        {'step_update': {'conversation_id': 'session', 'step_index': 2, 'step_type': 'agent_response', 'text_delta': 'world'}},
        {'step_update': {'conversation_id': 'session', 'step_index': 2, 'step_type': 'agent_response', 'state': 'DONE',
                         **({'usage': {'input_tokens': 5, 'output_tokens': 2, 'thinking_tokens': 1,
                                       'cache_read_tokens': 3, 'total_tokens': 7}} if done_usage else {})}},
        {'event': 'result', 'result': {'conversation_id': 'session', 'status': 'SUCCESS',
                                      'response': response, 'num_turns': 2,
                                      'usage': {'input_tokens': 999, 'output_tokens': 999}}},
    ]
    return rows


def project(rows, expected='hello world'):
    raw = ('\n'.join(json.dumps(row) for row in rows) + '\n').encode()
    return project_final_response_usage(raw, expected_response=expected, expected_session_id='session')


def test_final_usage_is_exact_response_step_not_internal_or_result_total():
    value = project(stream())
    assert value['response_step_index'] == 2
    assert value['usage'] == {'input_tokens': 5, 'output_tokens': 2, 'thinking_tokens': 1,
                              'cache_read_tokens': 3, 'total_tokens': 7}
    assert 'cache_write_tokens' not in value['usage']
    assert value['response_delta_lines'] == [3, 4]
    assert value['usage_line'] == 5


def test_response_text_must_match_result_and_expected_observer_exactly():
    with pytest.raises(ValueError, match='final step text'):
        project(stream(response='hello world!'), expected='hello world!')
    with pytest.raises(ValueError, match='result response'):
        project(stream(), expected='hello world!')


def test_missing_and_duplicate_final_usage_rejected():
    with pytest.raises(ValueError, match='missing final response usage'):
        project(stream(done_usage=False))
    rows = stream()
    rows.insert(-1, rows[-2])
    with pytest.raises(ValueError, match='duplicate final response completion'):
        project(rows)


def test_other_step_usage_cannot_substitute_for_final_usage():
    rows = stream(done_usage=False)
    rows[-1]['result']['usage']['cache_write_tokens'] = 0
    with pytest.raises(ValueError, match='missing final response usage'):
        project(rows)


def test_mismatched_or_duplicate_final_usage_rejected():
    rows = stream()
    rows[-2]['step_update']['usage']['total_tokens'] = 8
    with pytest.raises(ValueError, match='inconsistent final response total'):
        project(rows)
    raw = ('\n'.join(json.dumps(row) for row in stream()) + '\n').encode()
    raw = raw.replace(b'"total_tokens": 7}', b'"total_tokens": 7, "total_tokens": 7}')
    with pytest.raises(ValueError, match='duplicate stdout JSON key'):
        project_final_response_usage(raw, expected_response='hello world', expected_session_id='session')


def test_each_retained_stream_has_exact_bound_final_usage():
    root = Path(__file__).resolve().parents[1] / 'artifacts/v1-expanded-preparation/live-captures'
    for capture in sorted(root.glob('antigravity-2026-09-29-0[234]')):
        qualified = capture / 'qualification-v4'
        observer = json.loads((qualified / 'observer.json').read_bytes())
        responses = {event['id']: event['fields']['text'] for event in observer['events']
                     if event['kind'] == 'assistant_response'}
        session = json.loads((qualified / 'capture-result.json').read_bytes())['native_primary'].split('/')[0]
        for turn in (1, 2):
            raw = (qualified / f'r{turn}.stdout.jsonl').read_bytes()
            value = project_final_response_usage(raw, expected_response=responses[f'response-r{turn}'],
                                                 expected_session_id=session)
            assert value['usage']['total_tokens'] == value['usage']['input_tokens'] + value['usage']['output_tokens']
            assert 'cache_write_tokens' not in value['usage']
