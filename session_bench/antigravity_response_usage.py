"""Private, source-bound final-response usage from retained Antigravity stdout."""
from __future__ import annotations

import hashlib
import json


def project_final_response_usage(stdout: bytes, *, expected_response: str, expected_session_id: str) -> dict:
    """Accept only a unique completed response step with exact streamed text.

    The result usage is cumulative and is deliberately never read. Other
    agent_response steps are internal model work, not displayed responses.
    """
    def unique_pairs(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError('duplicate stdout JSON key')
            value[key] = item
        return value

    try:
        lines = stdout.decode('utf-8').splitlines()
        rows = [(line_no, json.loads(line, object_pairs_hook=unique_pairs,
                 parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value))))
                for line_no, line in enumerate(lines, 1) if line.strip()]
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError('invalid Antigravity stdout') from error
    initial = [row for _, row in rows if row.get('event') == 'init']
    results = [(line, row['result']) for line, row in rows if row.get('event') == 'result']
    if (len(initial) != 1 or initial[0].get('conversation_id') != expected_session_id
            or len(results) != 1 or results[0][1].get('conversation_id') != expected_session_id
            or results[0][1].get('status') != 'SUCCESS'):
        raise ValueError('missing or ambiguous Antigravity session boundary')
    result_line, result = results[0]
    if not isinstance(expected_response, str) or result.get('response') != expected_response:
        raise ValueError('result response differs from expected displayed response')

    steps: dict[int, dict] = {}
    for line, row in rows:
        step = row.get('step_update')
        if not isinstance(step, dict):
            continue
        if step.get('conversation_id') != expected_session_id:
            raise ValueError('cross-session response step')
        index = step.get('step_index')
        if type(index) is not int:
            raise ValueError('response step lacks integer index')
        item = steps.setdefault(index, {'type': step.get('step_type'), 'deltas': [], 'done': []})
        if item['type'] != step.get('step_type'):
            raise ValueError('response step type changed')
        if 'text_delta' in step:
            if item['type'] != 'agent_response' or not isinstance(step['text_delta'], str):
                raise ValueError('invalid response text delta')
            item['deltas'].append((line, step['text_delta']))
        if step.get('state') == 'DONE':
            item['done'].append((line, step))
    streamed = [(index, item) for index, item in steps.items() if item['deltas']]
    if len(streamed) != 1:
        raise ValueError('missing or ambiguous streamed final response')
    index, final = streamed[0]
    if ''.join(delta for _, delta in final['deltas']) != expected_response:
        raise ValueError('final step text differs from result response')
    if len(final['done']) != 1:
        raise ValueError('missing or duplicate final response completion')
    done_line, completion = final['done'][0]
    # Antigravity can put the final text_delta and DONE usage in one row.
    if done_line < final['deltas'][-1][0] or done_line >= result_line:
        raise ValueError('final response completion out of order')
    usage = completion.get('usage')
    if not isinstance(usage, dict):
        raise ValueError('missing final response usage')
    for key in ('input_tokens', 'output_tokens', 'thinking_tokens', 'cache_read_tokens', 'total_tokens'):
        if type(usage.get(key)) is not int or usage[key] < 0:
            raise ValueError(f'invalid final response {key}')
    if usage['total_tokens'] != usage['input_tokens'] + usage['output_tokens']:
        raise ValueError('inconsistent final response total tokens')
    for key, value in usage.items():
        if type(value) is not int or value < 0:
            raise ValueError(f'invalid final response {key}')
    return {
        'schema_version': 'session-bench-antigravity-final-response-usage-v1',
        'source_stdout_sha256': hashlib.sha256(stdout).hexdigest(),
        'source_size_bytes': len(stdout),
        'session_id': expected_session_id,
        'response_sha256': hashlib.sha256(expected_response.encode('utf-8')).hexdigest(),
        'response_step_index': index,
        'response_delta_lines': [line for line, _ in final['deltas']],
        'usage_line': done_line,
        'result_line': result_line,
        'usage': dict(usage),
        'scope': 'final_displayed_response_step_only',
    }
