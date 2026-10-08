"""Concrete native mutation controls from the independent DSH review."""
import copy

import pytest

from session_bench.dsh_live import DSHSemanticError, stdout_projection
from test_dsh_live import decode, rows


@pytest.mark.parametrize("kind", ["aborted", "error", "cancelled", None])
def test_noncompleted_turn_cannot_promote_final_response(tmp_path, kind):
    values = rows()
    values[-1]["data"]["reason"] = {"kind": kind}
    with pytest.raises(DSHSemanticError, match="unsupported turn completion"):
        decode(tmp_path, values)


@pytest.mark.parametrize("identity", ["u", "call", "", 1])
def test_tool_result_identity_must_be_native_nonempty_unique_string(tmp_path, identity):
    values = rows()
    values[4]["data"]["message"]["id"] = identity
    with pytest.raises(DSHSemanticError, match="result identity"):
        decode(tmp_path, values)


def with_tool_declaration():
    values = rows()
    declaration = copy.deepcopy(values[5])
    declaration["data"]["message"]["id"] = "tool-declaration"
    declaration["data"]["message"]["content"] = [{"type": "tool-call", "id": "call", "name": "bash", "arguments": '{"command":"echo hello"}'}]
    declaration["data"].pop("usage")
    values.insert(3, declaration)
    return values


def test_matching_native_tool_declaration_is_supported(tmp_path):
    assert decode(tmp_path, with_tool_declaration())["status"] == "ok"


@pytest.mark.parametrize("field,value", [("name", "edit"), ("arguments", '{"command":"different"}')])
def test_assistant_tool_block_cannot_disagree_with_native_call(tmp_path, field, value):
    values = with_tool_declaration()
    values[3]["data"]["message"]["content"][0][field] = value
    with pytest.raises(DSHSemanticError, match="native tool/call disagree"):
        decode(tmp_path, values)


def test_unconsumed_assistant_tool_declaration_is_incomplete(tmp_path):
    values = with_tool_declaration()
    values[3]["data"]["message"]["content"][0]["id"] = "unexecuted"
    assert decode(tmp_path, values)["status"] == "unsupported"


@pytest.mark.parametrize("cwd", [None, "", 5])
def test_missing_native_header_working_directory_rejected(tmp_path, cwd):
    values = rows()
    values[0]["cwd"] = cwd
    with pytest.raises(DSHSemanticError, match="working directory"):
        decode(tmp_path, values)


def test_unknown_stdout_tool_status_is_not_success():
    import json
    values = [{"type": "session", "sessionId": "s"},
              {"type": "tool_call", "callId": "c", "tool": "bash", "input": {"command": "echo hi"}},
              {"type": "tool_result", "callId": "c", "status": "future-unknown", "result": "hi"},
              {"type": "final", "text": "done"}]
    with pytest.raises(DSHSemanticError, match="outcome"):
        stdout_projection("\n".join(map(json.dumps, values)))
