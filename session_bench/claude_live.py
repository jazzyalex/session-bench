"""Claude Code CLI live-stream and native JSONL helpers.

The functions in this module are deliberately small seams around the frozen
survival-v1 observer and comparator.  They accept only the submitted Claude
stream or one already selected synthetic project-keyed JSONL file.  They do
not discover a Claude root, read credentials, or search historical sessions.
"""

from __future__ import annotations

import json
import re
import shlex
from pathlib import Path
from typing import Any, Mapping


class ClaudeLiveError(ValueError):
    """The supplied Claude stream or selected native file is not joinable."""


_SESSION_KEYS = ("session_id", "sessionId", "sessionID", "session")
_CANARY_RE = re.compile(r"SB_SURVIVAL_V1_RESPONSE_[^\s<`]+", re.UNICODE)
_RUN_CANARY_RE = re.compile(r"SB_SURVIVAL_V1_RUN_[A-Za-z0-9_-]+")
_EXIT_RE = re.compile(
    r"(?:process\s+)?exit(?:ed|s)?(?:\s+with)?(?:\s+code)?\s*[:=]?\s*(-?\d+)",
    re.IGNORECASE,
)
_HELPER_COMMAND_RE = re.compile(
    r"(?:^|[\s;&|])(?:python(?:3)?\s+)?(?:[./\w-]+/)?bench_check\.py"
    r"\s+(inspect|baseline|final)(?=\s|$|[;&|])",
    re.IGNORECASE,
)
_COMPOUND_EDIT_MARKERS = (
    "apply_patch",
    "cat >",
    "cat>>",
    "tee ",
    "sed -i",
    "perl -i",
    "python -c",
    "python3 -c",
    "python - <<",
    "python3 - <<",
    "mv ",
    "cp ",
)


def _strict_json(raw: str, label: str) -> Any:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in items:
            if key in value:
                raise ClaudeLiveError(f"{label}: duplicate JSON key {key!r}")
            value[key] = item
        return value

    try:
        return json.loads(raw, object_pairs_hook=pairs, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
    except ClaudeLiveError:
        raise
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
        raise ClaudeLiveError(f"{label}: invalid JSON") from exc


def _collect_sessions(value: Any, found: list[str]) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if key in _SESSION_KEYS and isinstance(child, str) and child.strip():
                found.append(child.strip())
            _collect_sessions(child, found)
    elif isinstance(value, list):
        for child in value:
            _collect_sessions(child, found)


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(_text(item.get("text", item.get("content", ""))) for item in value if isinstance(item, Mapping))
    if isinstance(value, Mapping):
        if isinstance(value.get("text"), str):
            return value["text"]
        if "content" in value:
            return _text(value["content"])
    return ""


def _message(row: Mapping[str, Any]) -> Mapping[str, Any]:
    message = row.get("message")
    return message if isinstance(message, Mapping) else row


def _content(row: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    content = _message(row).get("content")
    if isinstance(content, list):
        return [item for item in content if isinstance(item, Mapping)]
    return []


def _model(row: Mapping[str, Any]) -> str | None:
    message = _message(row)
    for value in (row.get("model"), message.get("model")):
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _usage(row: Mapping[str, Any]) -> dict[str, int] | None:
    message = _message(row)
    candidate: Any = row.get("usage")
    if not isinstance(candidate, Mapping):
        candidate = message.get("usage")
    if not isinstance(candidate, Mapping):
        return None

    def number(*names: str) -> int | None:
        for name in names:
            value = candidate.get(name)
            if type(value) is int and value >= 0:
                return value
        return None

    cache = candidate.get("cache")
    if not isinstance(cache, Mapping):
        cache = {}
    values = {
        "input": number("input_tokens", "input"),
        "output": number("output_tokens", "output"),
        "reasoning": number("reasoning_tokens", "reasoning"),
        "cache_read": number("cache_read_tokens", "cached_input_tokens", "cache_read"),
        "cache_write": number("cache_write_tokens", "cache_creation_input_tokens", "cache_write"),
    }
    if values["cache_read"] is None:
        values["cache_read"] = cache.get("read") if type(cache.get("read")) is int and cache.get("read") >= 0 else None
    if values["cache_write"] is None:
        values["cache_write"] = cache.get("write") if type(cache.get("write")) is int and cache.get("write") >= 0 else None
    if values["input"] is None or values["output"] is None:
        return None
    for key in ("reasoning", "cache_read", "cache_write"):
        if values[key] is None:
            values[key] = 0
    return {key: int(value) for key, value in values.items()}


def _tool_result_text(block: Mapping[str, Any]) -> str:
    return _text(block.get("content", block.get("output", block.get("result", ""))))


def _exit_code(output: str, is_error: Any) -> int:
    matches = list(_EXIT_RE.finditer(output))
    if matches:
        try:
            return int(matches[-1].group(1))
        except ValueError:
            pass
    return 1 if is_error is True else 0


def _normalize_command(command: str, run_canary: str | None = None) -> list[str]:
    try:
        parts = shlex.split(command)
    except ValueError as exc:
        raise ClaudeLiveError(f"Claude Bash command is not shell-parseable: {exc}") from exc
    if parts[:1] == ["cd"] and len(parts) >= 4:
        marker = next((index for index, value in enumerate(parts) if value in {"&&", ";"}), None)
        if marker is not None and marker >= 2 and parts[1].rstrip("/") in {"fixture_project", "./fixture_project"}:
            parts = parts[marker + 1 :]
    while parts and ("=" in parts[0] and parts[0].split("=", 1)[0].startswith("SB_")):
        parts.pop(0)
    if run_canary and "--run-canary" in parts:
        index = parts.index("--run-canary")
        if index + 1 >= len(parts) or parts[index + 1] != run_canary:
            # ``shlex.split`` leaves a shell terminator attached when the
            # model writes ``--run-canary VALUE; echo ...``.  The terminator
            # is command syntax, not part of the canary.  Strip only that
            # shell punctuation before comparing so a wrong value still
            # fails closed.
            candidate = parts[index + 1].rstrip(";&|") if index + 1 < len(parts) else ""
            if candidate != run_canary:
                raise ClaudeLiveError("Claude helper command carries the wrong run canary")
            parts[index + 1] = candidate
        del parts[index : index + 2]
    if not parts:
        raise ClaudeLiveError("Claude Bash command is empty")
    return parts


def _helper_phase(command: str) -> str | None:
    match = _HELPER_COMMAND_RE.search(command)
    return match.group(1).lower() if match else None


def _is_compound_edit(name: str, input_value: Mapping[str, Any] | str) -> bool:
    """Recognize an edit embedded in a Bash command that also runs the helper."""

    if name.strip().lower() not in {"bash", "shell", "command", "terminal"}:
        return False
    if isinstance(input_value, Mapping):
        command = input_value.get("command", input_value.get("cmd", input_value.get("command_line")))
    else:
        command = input_value
    if not isinstance(command, str):
        return False
    lowered = command.lower()
    if _helper_phase(command) != "final" or "checkout.py" not in lowered:
        return False
    return any(marker in lowered for marker in _COMPOUND_EDIT_MARKERS)


def _canonical_helper_command(command: str, run_canary: str) -> str | None:
    """Return the frozen helper invocation contained in a possibly compound command."""

    phase = _helper_phase(command)
    if phase is None:
        return None
    # Validate every explicit canary in the original command before projecting
    # the helper segment. This keeps a compound shell action from laundering a
    # wrong run identity while making the semantic helper action joinable.
    matches = re.findall(r"--run-canary\s+([^\s;&|]+)", command)
    if matches and any(value != run_canary for value in matches):
        raise ClaudeLiveError("Claude helper command carries the wrong run canary")
    return f"python3 bench_check.py {phase} --run-canary {shlex.quote(run_canary)}"


def _relative_target(value: Any, workspace: Path) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip().replace("\\", "/")
    workspace_text = workspace.resolve().as_posix().rstrip("/")
    if raw == workspace_text:
        return "."
    if raw.startswith(workspace_text + "/"):
        return raw[len(workspace_text) + 1 :]
    return raw


def _tool_input(block: Mapping[str, Any]) -> Mapping[str, Any] | str:
    value = block.get("input", block.get("arguments", block.get("args", {})))
    return value if isinstance(value, (Mapping, str)) else {}


def _tool_uses(row: Mapping[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for index, block in enumerate(_content(row)):
        if block.get("type") != "tool_use":
            continue
        tool_id = block.get("id")
        name = block.get("name")
        if not isinstance(tool_id, str) or not tool_id.strip() or not isinstance(name, str) or not name.strip():
            raise ClaudeLiveError("Claude tool_use lacks id or name")
        result.append({"id": tool_id, "name": name, "input": _tool_input(block), "index": index})
    return result


def _all_rows(raw: str, *, expected_session_id: str | None = None) -> tuple[list[dict[str, Any]], str, str | None]:
    if not isinstance(raw, str) or not raw.strip():
        raise ClaudeLiveError("Claude stream is empty")
    rows: list[dict[str, Any]] = []
    sessions: set[str] = set()
    model: str | None = None
    for number, line in enumerate(raw.splitlines(), 1):
        if not line.strip():
            raise ClaudeLiveError(f"Claude stream line {number} is blank")
        row = _strict_json(line, f"Claude stream line {number}")
        if not isinstance(row, dict):
            raise ClaudeLiveError(f"Claude stream line {number} is not an object")
        found: list[str] = []
        _collect_sessions(row, found)
        sessions.update(found)
        model = model or _model(row)
        rows.append(row)
    if len(sessions) != 1:
        raise ClaudeLiveError(f"Claude stream must contain exactly one session ID (found {len(sessions)})")
    session_id = next(iter(sessions))
    if expected_session_id is not None and session_id != expected_session_id:
        raise ClaudeLiveError("Claude stream session ID does not match continuation")
    return rows, session_id, model


def claude_stream_to_observer_jsonl(
    raw: str,
    *,
    turn: int,
    run_canary: str,
    expected_session_id: str | None = None,
    workspace: str | Path | None = None,
) -> dict[str, Any]:
    """Normalize one Claude stream into the existing live observer row shape."""
    if turn not in (1, 2):
        raise ClaudeLiveError("Claude stream turn must be 1 or 2")
    if not _RUN_CANARY_RE.fullmatch(run_canary):
        raise ClaudeLiveError("invalid survival run canary")
    rows, session_id, model = _all_rows(raw, expected_session_id=expected_session_id)
    workspace_path = Path(workspace).resolve() if workspace is not None else None

    results: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        for block in _content(row):
            if block.get("type") != "tool_result":
                continue
            tool_id = block.get("tool_use_id", block.get("toolUseId"))
            if isinstance(tool_id, str) and tool_id.strip():
                results[tool_id] = block

    output_rows: list[dict[str, Any]] = []
    response_canary_seen = False
    for row in rows:
        for use in _tool_uses(row):
            tool_id = use["id"]
            result = results.get(tool_id)
            input_value = use["input"]
            output = _tool_result_text(result) if result is not None else "completed"
            exit_code = _exit_code(output, result.get("is_error") if result else False)
            state = {"status": "completed", "output": output, "metadata": {"exit_code": exit_code}}
            compound_edit = _is_compound_edit(use["name"], input_value)
            command = None
            if isinstance(input_value, Mapping):
                candidate = input_value.get("command", input_value.get("cmd", input_value.get("command_line")))
                if isinstance(candidate, str):
                    command = candidate
            elif isinstance(input_value, str):
                command = input_value
            projected_input: Mapping[str, Any] | str = input_value
            if command is not None:
                helper_command = _canonical_helper_command(command, run_canary)
                if helper_command is not None:
                    projected_input = {"command": helper_command}
            if compound_edit:
                output_rows.append({
                    "type": "tool_use",
                    "tool": "Edit",
                    "input": {
                        "file_path": "fixture_project/checkout.py",
                        "compound_parent_tool_use_id": tool_id,
                    },
                    "callID": f"{tool_id}:compound-edit",
                    "id": f"{tool_id}:compound-edit",
                    "sessionID": session_id,
                    "cwd": workspace_path.as_posix() if workspace_path is not None else "fixture_project",
                    "target": "fixture_project/checkout.py",
                    "state": {
                        "status": "completed",
                        "output": "compound edit completed",
                        "metadata": {"exit_code": 0, "compound_parent_tool_use_id": tool_id},
                    },
                    "compound": True,
                    "compound_parent_tool_use_id": tool_id,
                })
            generic: dict[str, Any] = {
                "type": "tool_use",
                "tool": use["name"],
                "input": projected_input,
                "callID": tool_id,
                "id": tool_id,
                "sessionID": session_id,
                "state": state,
            }
            if compound_edit:
                generic["compound"] = True
                generic["compound_edit_expanded"] = True
            if workspace_path is not None:
                generic["cwd"] = workspace_path.as_posix()
            if isinstance(projected_input, Mapping):
                target = projected_input.get("file_path", projected_input.get("path", projected_input.get("target")))
                if workspace_path is not None and isinstance(target, str):
                    generic["target"] = _relative_target(target, workspace_path)
            output_rows.append(generic)
        message = _message(row)
        role = message.get("role", row.get("role"))
        content = message.get("content")
        # User prompts carry the required response canary as part of the
        # frozen text, so they must never be projected as visible assistant
        # responses. Tool-result rows are represented by tool state only.
        text_blocks = []
        if role == "assistant":
            text_blocks = [block.get("text") for block in content if isinstance(block, Mapping) and block.get("type") == "text" and isinstance(block.get("text"), str) and block.get("text")] if isinstance(content, list) else ([content] if isinstance(content, str) and content else [])
        for text_value in text_blocks:
            text_value = str(text_value)
            if "SB_SURVIVAL_V1_RESPONSE_" in text_value:
                response_canary_seen = True
            output_rows.append({"type": "text", "text": text_value, "sessionID": session_id, "model": model or "model-unreported"})
        if row.get("type") == "result":
            result_text = row.get("result")
            if isinstance(result_text, str) and result_text and "SB_SURVIVAL_V1_RESPONSE_" in result_text and not response_canary_seen:
                response_canary_seen = True
                output_rows.append({"type": "text", "text": result_text, "sessionID": session_id, "model": model or "model-unreported"})
            usage = _usage(row)
            if usage is not None:
                output_rows.append({
                    "type": "step_finish",
                    "reason": "stop",
                    "tokens": {
                        "input_tokens": usage["input"],
                        "output_tokens": usage["output"],
                        "reasoning_tokens": usage["reasoning"],
                        "cache_read_tokens": usage["cache_read"],
                        "cache_write_tokens": usage["cache_write"],
                    },
                    "sessionID": session_id,
                })
    return {"session_id": session_id, "model": model, "rows": output_rows}


def _response_texts(row: Mapping[str, Any]) -> list[str]:
    message = _message(row)
    content = message.get("content")
    if isinstance(content, str) and content:
        return [content]
    if isinstance(content, list):
        return [str(block["text"]) for block in content if isinstance(block, Mapping) and block.get("type") == "text" and isinstance(block.get("text"), str) and block.get("text")]
    return []


def _response_canary(text: str) -> str | None:
    match = _CANARY_RE.search(text)
    return match.group(0).rstrip(".,;:)]}") if match else None


def native_facts_from_claude_session(
    session_bytes: bytes,
    *,
    run_canary: str,
    workspace: str | Path,
    before_sha256: str,
    after_sha256: str,
    usage_mode: str = "full",
) -> dict[str, Any]:
    """Project one selected Claude JSONL transcript into comparator facts.

    The raw transcript is the only input.  Paths and whole-file hashes are
    normalized to the frozen workload's relative boundary so the comparator
    can join native facts to its independent observer.
    """
    if usage_mode not in {"full", "opaque", "none"}:
        raise ClaudeLiveError("usage_mode must be 'full', 'opaque', or 'none'")
    try:
        raw = session_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ClaudeLiveError("selected Claude session is not UTF-8") from exc
    rows, session_id, model = _all_rows(raw)
    workspace_path = Path(workspace).resolve()
    turns: list[dict[str, Any]] = []
    responses: list[dict[str, Any]] = []
    actions: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []
    relations: list[dict[str, Any]] = []
    usage: list[dict[str, Any]] = []
    action_by_tool: dict[str, dict[str, Any]] = {}
    compound_edit_by_tool: dict[str, dict[str, Any]] = {}
    result_by_tool: dict[str, dict[str, Any]] = {}
    turn_responses: dict[str, list[str]] = {}
    final_canaries_by_turn: dict[str, set[str]] = {}
    usage_message_ids: set[str] = set()
    active_turn: str | None = None
    sequence = 0
    session_totals: dict[str, int] | None = None
    result_total_rows: list[dict[str, int]] = []

    def row_uuid(row: Mapping[str, Any], fallback: str) -> str:
        value = row.get("uuid")
        return value if isinstance(value, str) and value.strip() else fallback

    def message_uuid(row: Mapping[str, Any], fallback: str) -> str:
        message = _message(row)
        for candidate in (message.get("id"), row.get("message_id"), row.get("uuid")):
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
        return fallback

    for line_number, row in enumerate(rows, 1):
        sequence += 1
        message = _message(row)
        role = message.get("role", row.get("role"))
        content = message.get("content")
        if row.get("type") == "user" and isinstance(content, str):
            active_turn = row_uuid(row, f"user-{line_number}")
            revision = "r2" if "Correction R2" in content or re.search(r"\bR2\b", content) else "r1"
            turns.append({
                "id": active_turn,
                "turn_id": active_turn,
                "role": "user",
                "revision": revision,
                "text": content,
                "sequence": sequence,
            })
        if role == "assistant":
            for use in _tool_uses(row):
                input_value = use["input"]
                name = str(use["name"])
                compound_edit = _is_compound_edit(name, input_value)
                action: dict[str, Any] = {
                    "id": use["id"],
                    "call_id": use["id"],
                    "tool_name": name,
                    "name": name,
                    "input": input_value,
                    "turn_id": active_turn,
                    "sequence": sequence,
                    "cwd": "fixture_project",
                }
                target: str | None = None
                if isinstance(input_value, Mapping):
                    raw_target = input_value.get("file_path", input_value.get("path", input_value.get("target")))
                    target = _relative_target(raw_target, workspace_path)
                command: str | None = None
                if isinstance(input_value, Mapping) and isinstance(input_value.get("command"), str):
                    command = input_value["command"]
                elif isinstance(input_value, str):
                    command = input_value
                if command is not None:
                    argv = _normalize_command(command, run_canary)
                    if "bench_check.py" in argv:
                        phase = next((value for value in ("inspect", "baseline", "final") if value in argv), None)
                        if phase is not None:
                            argv = ["python3", "bench_check.py", phase]
                            target = "fixture_project/checkout.py"
                            action["cwd"] = "fixture_project"
                    action["argv"] = argv
                elif name.lower() in {"edit", "write", "apply_patch", "patch"}:
                    target = target or "fixture_project/checkout.py"
                    action["argv"] = ["replace_function", target]
                    action["cwd"] = "fixture_project"
                if target is not None:
                    action["target"] = target
                if compound_edit:
                    compound_id = f"{use['id']}:compound-edit"
                    compound_action = {
                        "id": compound_id,
                        "call_id": compound_id,
                        "tool_name": "Edit",
                        "name": "Edit",
                        "input": {
                            "file_path": "fixture_project/checkout.py",
                            "compound_parent_tool_use_id": use["id"],
                        },
                        "argv": ["replace_function", "fixture_project/checkout.py"],
                        "target": "fixture_project/checkout.py",
                        "turn_id": active_turn,
                        "sequence": sequence,
                        "cwd": "fixture_project",
                        "compound": True,
                        "compound_parent_action_id": use["id"],
                    }
                    compound_edit_by_tool[use["id"]] = compound_action
                    actions.append(compound_action)
                    action["compound_edit_action_id"] = compound_id
                action_by_tool[use["id"]] = action
                actions.append(action)
            response_texts = _response_texts(row)
            row_usage = _usage(row)
            for block_index, text_value in enumerate(response_texts):
                canary = _response_canary(text_value)
                response_id = f"{row_uuid(row, f'assistant-{line_number}')}-block-{block_index}"
                response_message_id = message_uuid(row, response_id)
                already_final = canary is not None and canary in final_canaries_by_turn.get(active_turn or "", set())
                response: dict[str, Any] = {
                    "id": response_id,
                    "turn_id": active_turn,
                    "role": "assistant",
                    "status": "completed",
                    "phase": "intermediate" if already_final else ("final_answer" if canary else "intermediate"),
                    "text": text_value,
                    "message_id": response_message_id,
                    "model_id": _model(row) or model or "model-unreported",
                    "configuration": _model(row) or model or "model-unreported",
                    "sequence": sequence,
                }
                if canary and not already_final:
                    response["canary"] = canary
                    turn_responses.setdefault(active_turn or "", []).append(response_id)
                    final_canaries_by_turn.setdefault(active_turn or "", set()).add(canary)
                    if usage_mode != "none" and row_usage is not None:
                        usage_payload = {
                            "input_tokens": row_usage["input"],
                            "output_tokens": row_usage["output"],
                            "reasoning_tokens": row_usage["reasoning"],
                            "cache_read_tokens": row_usage["cache_read"],
                            "cache_write_tokens": row_usage["cache_write"],
                        }
                        if usage_mode == "opaque":
                            usage_payload = {}
                        if response_message_id not in usage_message_ids:
                            usage_id = f"usage-{len(usage) + 1}"
                            usage_message_ids.add(response_message_id)
                            usage.append({
                                "id": usage_id,
                                "usage_id": usage_id,
                                "response_id": response_id,
                                "turn_id": active_turn,
                                "message_id": response_message_id,
                                "usage": usage_payload,
                                **({"usage_opaque": True} if usage_mode == "opaque" else {}),
                            })
                responses.append(response)
        for block in _content(row):
            if block.get("type") != "tool_result":
                continue
            tool_id = block.get("tool_use_id", block.get("toolUseId"))
            if not isinstance(tool_id, str) or tool_id not in action_by_tool:
                continue
            output = _tool_result_text(block)
            code = _exit_code(output, block.get("is_error"))
            action = action_by_tool[tool_id]
            compound_action = compound_edit_by_tool.get(tool_id)
            if compound_action is not None:
                compound_id = str(compound_action["id"])
                compound_result = {
                    "id": f"{compound_id}:result",
                    "native_result_id": f"{compound_id}:result",
                    "action_id": compound_id,
                    "call_id": compound_id,
                    "turn_id": compound_action.get("turn_id"),
                    "status": "success",
                    "exit_code": 0,
                    "output": "compound edit completed",
                    "sequence": sequence,
                    "changed_path": "fixture_project/checkout.py",
                    "compound": True,
                    "compound_parent_result_id": f"{tool_id}:result",
                }
                results.append(compound_result)
                relations.append({
                    "id": f"relation-{compound_id}-result",
                    "kind": "action_result",
                    "from_id": compound_id,
                    "to_id": compound_result["id"],
                    "sequence": len(relations) + 1,
                })
            result: dict[str, Any] = {
                "id": f"{tool_id}:result",
                "native_result_id": f"{tool_id}:result",
                "action_id": tool_id,
                "call_id": tool_id,
                "turn_id": action.get("turn_id"),
                "status": "failure" if code != 0 else "success",
                "exit_code": code,
                "output": output,
                "sequence": sequence,
            }
            match = re.search(r"(?m)^(SB_SURVIVAL_V1_HELPER_(?:INSPECT|BASELINE|FINAL)_([^\s]+))", output)
            if match:
                result["helper_nonce"] = match.group(2)
                result["output_prefix"] = match.group(1)
            if action.get("tool_name", "").lower() in {"edit", "write", "apply_patch", "patch"}:
                result["changed_path"] = "fixture_project/checkout.py"
            result_by_tool[tool_id] = result
            results.append(result)
            relations.append({"id": f"relation-{tool_id}", "kind": "action_result", "from_id": tool_id, "to_id": result["id"], "sequence": len(relations) + 1})
        if row.get("type") == "result":
            total = _usage(row)
            if total is not None:
                result_total = {
                    "input": total["input"],
                    "output": total["output"],
                    "reasoning": total["reasoning"],
                    "cache_read": total["cache_read"],
                    "cache_write": total["cache_write"],
                }
                result_total_rows.append(result_total)
            result_text = row.get("result")
            canary = _response_canary(result_text) if isinstance(result_text, str) else None
            if canary and canary not in final_canaries_by_turn.get(active_turn or "", set()):
                response_id = f"{row_uuid(row, f'result-{line_number}')}-result-response"
                response_message_id = message_uuid(row, response_id)
                response = {
                    "id": response_id,
                    "turn_id": active_turn,
                    "role": "assistant",
                    "status": "completed",
                    "phase": "final_answer",
                    "text": result_text,
                    "canary": canary,
                    "message_id": response_message_id,
                    "model_id": _model(row) or model or "model-unreported",
                    "configuration": _model(row) or model or "model-unreported",
                    "sequence": sequence,
                }
                final_canaries_by_turn.setdefault(active_turn or "", set()).add(canary)
                turn_responses.setdefault(active_turn or "", []).append(response_id)
                if usage_mode != "none" and total is not None and response_message_id not in usage_message_ids:
                    usage_id = f"usage-{len(usage) + 1}"
                    usage_message_ids.add(response_message_id)
                    usage_payload = {
                        "input_tokens": total["input"],
                        "output_tokens": total["output"],
                        "reasoning_tokens": total["reasoning"],
                        "cache_read_tokens": total["cache_read"],
                        "cache_write_tokens": total["cache_write"],
                    }
                    if usage_mode == "opaque":
                        usage_payload = {}
                    usage.append({
                        "id": usage_id,
                        "usage_id": usage_id,
                        "response_id": response_id,
                        "turn_id": active_turn,
                        "message_id": response_message_id,
                        "usage": usage_payload,
                        **({"usage_opaque": True} if usage_mode == "opaque" else {}),
                    })
                responses.append(response)

    if usage_mode == "full" and result_total_rows:
        usage_totals = {
            key: sum(item["usage"][f"{key}_tokens"] for item in usage)
            for key in ("input", "output", "reasoning", "cache_read", "cache_write")
        }
        last_total = result_total_rows[-1]
        summed_totals = {key: sum(item[key] for item in result_total_rows) for key in last_total}
        if all(last_total[key] == usage_totals[key] for key in last_total):
            session_totals = last_total
        elif all(summed_totals[key] == usage_totals[key] for key in summed_totals):
            session_totals = summed_totals
        else:
            session_totals = last_total

    final_responses = [response for response in responses if response.get("phase") == "final_answer"]
    for turn in turns:
        response_ids = turn_responses.get(str(turn["id"]), [])
        if response_ids:
            relations.append({"id": f"relation-turn-{turn['id']}", "kind": "turn_response", "from_id": turn["id"], "to_id": response_ids[-1], "sequence": len(relations) + 1})
    if len(turns) >= 2:
        relations.append({"id": "relation-r1-r2", "kind": "supersedes", "from_id": turns[0]["id"], "to_id": turns[1]["id"], "sequence": len(relations) + 1})
    final_actions = [action for action in actions if action.get("argv", [None, None, None])[-1] == "final"]
    if len(turns) >= 2 and final_actions:
        relations.append({"id": "relation-final-after-r2", "kind": "final_after", "from_id": turns[1]["id"], "to_id": final_actions[-1]["id"], "sequence": len(relations) + 1})

    edit_actions = [action for action in actions if action.get("tool_name", "").lower() in {"edit", "write", "apply_patch", "patch"}]
    file_changes: list[dict[str, Any]] = []
    if edit_actions:
        file_changes.append({
            "id": f"file-change-{edit_actions[-1]['id']}",
            "path": "fixture_project/checkout.py",
            "before_sha256": before_sha256,
            "after_sha256": after_sha256,
            "action_id": edit_actions[-1]["id"],
        })

    facts: dict[str, Any] = {}
    if session_totals is not None and usage:
        facts["reconciliation"] = {"matches_session_totals": all(
            sum(item["usage"][key] for item in usage) == session_totals[name]
            for key, name in (
                ("input_tokens", "input"),
                ("output_tokens", "output"),
                ("reasoning_tokens", "reasoning"),
                ("cache_read_tokens", "cache_read"),
                ("cache_write_tokens", "cache_write"),
            )
        )}
        facts["usage"] = {"session_totals": session_totals}

    return {
        "format": "claude-code-jsonl-v1",
        "supported": True,
        "complete": True,
        "session_id": session_id,
        "turns": turns,
        "responses": final_responses,
        "actions": actions,
        "results": results,
        "file_changes": file_changes,
        "relations": relations,
        "usage": usage,
        "facts": facts,
        "diagnostics": [],
        "run_canary": run_canary,
    }


__all__ = [
    "ClaudeLiveError",
    "claude_stream_to_observer_jsonl",
    "native_facts_from_claude_session",
]
