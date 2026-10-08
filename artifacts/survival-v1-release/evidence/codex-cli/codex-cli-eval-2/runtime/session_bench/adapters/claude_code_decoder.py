"""Fail-closed decoder for a copied Claude Code project-keyed JSONL session.

The decoder accepts one explicitly declared copied package.  It never searches
``~/.claude`` or any Desktop profile and cannot silently attach companions.
This keeps a fresh synthetic CLI session useful for format qualification while
leaving normal-account history outside the evidence boundary.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any


FORMAT = "claude-code-jsonl-v1"
MANIFEST_NAME = "decode.json"
SESSION_NAME = "session.jsonl"


class ClaudeCodeDecoderError(ValueError):
    """The supplied copied package is not a closed Claude Code JSONL bundle."""


def _strict_json(text: str, source: str) -> Any:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise ClaudeCodeDecoderError(f"{source}: duplicate JSON key {key!r}")
            result[key] = value
        return result

    try:
        return json.loads(text, object_pairs_hook=pairs, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
    except ClaudeCodeDecoderError:
        raise
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
        raise ClaudeCodeDecoderError(f"{source}: invalid JSON") from exc


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _validate_package(package: Path) -> bytes:
    package = Path(package)
    if package.is_symlink() or not package.is_dir():
        raise ClaudeCodeDecoderError("copied package must be an ordinary directory")
    package = package.resolve()
    manifest_path = package / MANIFEST_NAME
    session_path = package / SESSION_NAME
    if not manifest_path.is_file() or manifest_path.is_symlink():
        raise ClaudeCodeDecoderError("copied package is missing decode.json")
    if not session_path.is_file() or session_path.is_symlink():
        raise ClaudeCodeDecoderError("copied package is missing session.jsonl")
    manifest = _strict_json(manifest_path.read_text(encoding="utf-8"), MANIFEST_NAME)
    if not isinstance(manifest, dict) or set(manifest) != {"format", "artifacts"} or manifest.get("format") != FORMAT:
        raise ClaudeCodeDecoderError("unsupported Claude Code package format")
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list) or len(artifacts) != 1 or not isinstance(artifacts[0], dict):
        raise ClaudeCodeDecoderError("package must declare exactly one session artifact")
    artifact = artifacts[0]
    if set(artifact) != {"id", "path", "sha256", "size_bytes", "depends_on"}:
        raise ClaudeCodeDecoderError("session artifact declaration has unexpected fields")
    if artifact["id"] != "session" or artifact["path"] != SESSION_NAME or artifact["depends_on"] != []:
        raise ClaudeCodeDecoderError("session artifact declaration is invalid")
    session_bytes = session_path.read_bytes()
    if artifact["size_bytes"] != len(session_bytes) or artifact["sha256"] != hashlib.sha256(session_bytes).hexdigest():
        raise ClaudeCodeDecoderError("session artifact digest mismatch")
    declared = {MANIFEST_NAME, SESSION_NAME}
    for candidate in package.rglob("*"):
        if candidate.is_symlink():
            raise ClaudeCodeDecoderError("symlinks are not allowed in copied packages")
        if candidate.is_file() and candidate.relative_to(package).as_posix() not in declared:
            raise ClaudeCodeDecoderError("copied package contains undeclared files")
    return session_bytes


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return "".join(item.get("text", "") for item in content if isinstance(item, dict) and item.get("type") == "text" and isinstance(item.get("text"), str))


_SHELL_TOOLS = {"bash", "shell", "command", "terminal"}
_SHELL_KEYWORDS = {"if", "then", "else", "elif", "fi", "for", "while", "until", "do", "done", "case", "esac", "function", "select", "time", "!"}
_HELPER_SEGMENT_RE = re.compile(r"python3?\s+(?:[./\w-]+/)?bench_check\.py\s+(inspect|baseline|final)\s+--run-canary\s+(\S+)")
_EXIT_ECHO_RE = re.compile(r"echo\s+(?:\"exit=\$\?\"|exit=\$\?)")
_EXIT_LINE_RE = re.compile(r"exit=(-?\d+)")
_CAT_WRITE_RE = re.compile(r"cat\s*(?:>\s*\S+\s*<<\s*\S+|<<\s*\S+\s*>\s*\S+)")
# The file the frozen workload asks the agent to edit, and the file its helper checks.
WORKLOAD_TARGET = "fixture_project/checkout.py"
_TARGET_NAME = WORKLOAD_TARGET.rsplit("/", 1)[1]


def _input_command(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("command", "cmd", "command_line"):
            command = value.get(key)
            if isinstance(command, str) and command.strip():
                return command
    return None


def split_shell_segments(command: str) -> list[dict[str, Any]] | None:
    """Top-level command segments of one shell command, in order.

    Segments are separated by ``;``, ``&&``, ``||`` or a newline outside quotes.
    A heredoc body belongs to the segment that opens it. The result is None for
    a command that cannot be segmented safely: an open quote or heredoc, a
    subshell, a brace group, command substitution, a background job or a shell
    keyword. Such a command stays one call.
    """
    segments: list[dict[str, Any]] = []
    current: dict[str, Any] = {"chars": [], "heredocs": []}
    pending: list[dict[str, Any]] = []
    quote: str | None = None
    index, size = 0, len(command)

    def close(separator: str | None) -> bool:
        nonlocal current
        text = "".join(current["chars"]).strip()
        if text:
            if text.split()[0] in _SHELL_KEYWORDS:
                return False
            segments.append({"index": len(segments), "text": text, "heredocs": current["heredocs"], "separator": separator})
        elif current["heredocs"]:
            return False
        current = {"chars": [], "heredocs": []}
        return True

    while index < size:
        char = command[index]
        following = command[index + 1] if index + 1 < size else ""
        if quote == "'":
            quote = None if char == "'" else quote
        elif quote == '"':
            if char == "`" or (char == "$" and following == "("):
                return None
            if char == "\\" and following:
                current["chars"].append(char)
                index += 1
                char = command[index]
            elif char == '"':
                quote = None
        elif char == "\\":
            if not following:
                return None
            if following == "\n":
                index += 2
                continue
            current["chars"].append(char)
            index += 1
            char = command[index]
        elif char in "'\"":
            quote = char
        elif char in "`(){}":
            return None
        elif char == "#" and (not current["chars"] or current["chars"][-1] in " \t"):
            while index < size and command[index] != "\n":
                index += 1
            continue
        elif char == "<" and following == "<" and command[index + 2:index + 3] != "<":
            end = index + 2
            strip_tabs = command[end:end + 1] == "-"
            end += 1 if strip_tabs else 0
            while command[end:end + 1] in (" ", "\t"):
                end += 1
            opener = command[end:end + 1]
            if opener and opener in "'\"":
                closing = command.find(opener, end + 1)
                if closing < 0:
                    return None
                delimiter, quoted, end = command[end + 1:closing], True, closing + 1
            else:
                start = end
                while end < size and command[end] not in " \t\n;&|<>'\"\\":
                    end += 1
                if command[end:end + 1] in ("'", '"', "\\"):
                    return None
                delimiter, quoted = command[start:end], False
            if not delimiter:
                return None
            heredoc = {"delimiter": delimiter, "quoted": quoted, "strip_tabs": strip_tabs, "body": ""}
            current["heredocs"].append(heredoc)
            pending.append(heredoc)
            current["chars"].append(command[index:end])
            index = end
            continue
        elif char == "<" and following == "<":
            current["chars"].append("<<<")
            index += 3
            continue
        elif char == "\n":
            index += 1
            for heredoc in pending:
                body: list[str] = []
                while True:
                    if index >= size:
                        return None
                    stop = command.find("\n", index)
                    stop = size if stop < 0 else stop
                    line = command[index:stop]
                    index = min(stop + 1, size)
                    if (line.lstrip("\t") if heredoc["strip_tabs"] else line) == heredoc["delimiter"]:
                        break
                    body.append(line + "\n")
                heredoc["body"] = "".join(body)
            pending = []
            if not close("\n"):
                return None
            continue
        elif char == ";":
            if following == ";" or not close(";"):
                return None
            index += 1
            continue
        elif char == "&":
            previous = current["chars"][-1] if current["chars"] else ""
            if following == "&":
                if not close("&&"):
                    return None
                index += 2
                continue
            if previous != ">" and following != ">":
                return None
        elif char == "|" and following == "|":
            if not close("||"):
                return None
            index += 2
            continue
        current["chars"].append(char)
        index += 1
    if quote is not None or pending or not close(None):
        return None
    return segments


def _redirect_targets(text: str) -> list[tuple[str, bool]]:
    """(path, appends) of every output redirection of one segment, outside quotes."""
    targets: list[tuple[str, bool]] = []
    quote: str | None = None
    index, size = 0, len(text)
    while index < size:
        char = text[index]
        if quote is not None:
            if char == "\\" and quote == '"':
                index += 1
            elif char == quote:
                quote = None
        elif char == "\\":
            index += 1
        elif char in "'\"":
            quote = char
        elif char == ">" and text[index - 1:index] != "<":
            appends = text[index + 1:index + 2] == ">"
            start = index + (2 if appends else 1)
            if text[start:start + 1] == "|":
                start += 1
            while text[start:start + 1] in (" ", "\t"):
                start += 1
            end = start
            while end < size and text[end] not in " \t;&|<>":
                end += 1
            word = text[start:end]
            if len(word) >= 2 and word[0] == word[-1] and word[0] in "'\"":
                word = word[1:-1]
            if word and text[start:start + 1] != "&" and not any(mark in word for mark in "'\"\\$*?"):
                targets.append((word, appends))
            index = end
            continue
        index += 1
    return targets


def classify_shell_segment(segment: dict[str, Any]) -> dict[str, Any] | None:
    """What one segment records: a frozen helper invocation, a write of the workload file, or nothing scored.

    A helper segment is exactly ``python3 bench_check.py <phase> --run-canary <canary>``.
    A write segment redirects its output to the workload file. Its ``content`` is
    the written file only for ``cat > file <<'DELIMITER'``: one quoted heredoc
    (no expansion) that replaces the file.
    """
    text = segment["text"]
    helper = _HELPER_SEGMENT_RE.fullmatch(text)
    if helper is not None and not segment["heredocs"]:
        canary = helper.group(2)
        if len(canary) >= 2 and canary[0] == canary[-1] and canary[0] in "'\"":
            canary = canary[1:-1]
        return {"kind": "helper", "phase": helper.group(1).lower(), "run_canary": canary}
    writes = [(path, appends) for path, appends in _redirect_targets(text) if path.rsplit("/", 1)[-1] == _TARGET_NAME]
    if not writes:
        return None
    content = None
    heredocs = segment["heredocs"]
    if (len(writes) == 1 and not writes[0][1] and len(_redirect_targets(text)) == 1 and _CAT_WRITE_RE.fullmatch(text)
            and len(heredocs) == 1 and heredocs[0]["quoted"] and not heredocs[0]["strip_tabs"]):
        content = heredocs[0]["body"]
    return {"kind": "write", "path": writes[-1][0], "content": content}


def compound_shell_segments(name: Any, input_value: Any) -> list[dict[str, Any]] | None:
    """The scored segments of a compound shell call, or None when the call is not compound.

    Compound shell call rule: a native shell call whose command holds two or
    more scored segments (helper invocations and writes of the workload file)
    is the native record of each of them. Each returned segment carries its
    index in the whole command and ``follows`` (the next segment's text, None
    for the last segment). A call with fewer scored segments stays one call.
    """
    if not isinstance(name, str) or name.strip().lower() not in _SHELL_TOOLS:
        return None
    command = _input_command(input_value)
    segments = split_shell_segments(command) if command is not None else None
    if segments is None:
        return None
    scored = []
    for position, segment in enumerate(segments):
        role = classify_shell_segment(segment)
        if role is not None:
            follows = segments[position + 1]["text"] if position + 1 < len(segments) else None
            scored.append({**segment, **role, "follows": follows})
    return scored if len(scored) >= 2 else None


def helper_segment_results(segments: list[dict[str, Any]], output: str, is_error: Any) -> dict[int, dict[str, Any]]:
    """Native result of each helper segment of one compound call, by segment index.

    The output is the helper line with that phase prefix in the native tool
    result. The exit code is the ``exit=N`` line directly after it when the
    command echoes ``exit=$?`` directly after the segment. Without that echo
    the call's own status is used only for the last segment of the call.
    A helper returned zero when an unbroken ``&&`` chain leads from it to a
    later helper whose own output line is present: the shell runs that helper
    only after a zero exit. Otherwise the exit code is None: it is never
    guessed. A phase whose output lines do not pair one-to-one with its
    segments has no result.
    """
    scored = [segment if "kind" in segment else {**segment, **(classify_shell_segment(segment) or {}),
                                                "follows": segments[position + 1]["text"] if position + 1 < len(segments) else None}
              for position, segment in enumerate(segments)]
    lines = output.split("\n") if isinstance(output, str) else []
    results: dict[int, dict[str, Any]] = {}
    for phase in ("inspect", "baseline", "final"):
        helpers = [segment for segment in scored if segment.get("kind") == "helper" and segment["phase"] == phase]
        prefix = f"SB_SURVIVAL_V1_HELPER_{phase.upper()}_"
        found = [number for number, line in enumerate(lines) if line.startswith(prefix)]
        if not helpers or len(helpers) != len(found):
            continue
        for segment, number in zip(helpers, found):
            exit_code: int | None = None
            if segment["follows"] is not None and _EXIT_ECHO_RE.fullmatch(segment["follows"]):
                echoed = _EXIT_LINE_RE.fullmatch(lines[number + 1].strip()) if number + 1 < len(lines) else None
                exit_code = int(echoed.group(1)) if echoed else None
            elif segment["follows"] is None:
                exit_code = 1 if is_error is True else 0
            results[segment["index"]] = {"output": lines[number], "exit_code": exit_code}
    for position, segment in enumerate(scored):
        result = results.get(segment["index"])
        if result is None or result["exit_code"] is not None:
            continue
        later = position
        while later + 1 < len(scored) and scored[later].get("separator") == "&&":
            later += 1
            if scored[later]["index"] in results:
                result["exit_code"] = 0
                break
    return results


def _event(record: dict[str, Any], *, kind: str, text: str, line_number: int, block_index: int | None = None, tool_use_id: str | None = None) -> dict[str, Any]:
    suffix = "" if block_index is None else f"-block-{block_index}"
    return {
        "id": f"{record.get('uuid', f'line-{line_number}')}{suffix}",
        "kind": kind,
        "session_id": record["sessionId"],
        "timestamp": record.get("timestamp"),
        "text": text,
        "tool_use_id": tool_use_id,
        "locator": {"artifact": SESSION_NAME, "line": line_number, **({"block": block_index} if block_index is not None else {})},
    }


def _segment_event(parent: dict[str, Any], segment: dict[str, Any], text: str) -> dict[str, Any]:
    """One event of a compound shell call: the parent's native line and block, its own stable identity."""
    event = dict(parent)
    event["id"] = f"{parent['id']}-segment-{segment['index']}"
    event["text"] = text
    event["tool_use_id"] = f"{parent['tool_use_id']}:segment-{segment['index']}"
    event["parent_tool_use_id"] = parent["tool_use_id"]
    event["segment_index"] = segment["index"]
    return event


def _events_for_record(
    record: dict[str, Any],
    message: dict[str, Any],
    line_number: int,
    compound_calls: dict[str, list[dict[str, Any]]] | None = None,
) -> list[dict[str, Any]]:
    """Expand tool blocks so one JSONL message cannot collapse benchmark populations.

    A compound shell call (see ``compound_shell_segments``) yields one action
    per scored segment and one result per helper segment whose output line is
    in the native tool result. A write segment has no result of its own. Every
    event text is native text; no record is invented.
    """

    compound_calls = compound_calls or {}
    record_type = record["type"]
    content = message.get("content")
    if record_type == "user":
        if isinstance(content, str):
            return [_event(record, kind="submitted_turn", text=content, line_number=line_number)]
        if not isinstance(content, list):
            return []
        result = []
        for index, block in enumerate(content):
            if isinstance(block, dict) and block.get("type") == "tool_result":
                tool_id = block.get("tool_use_id", block.get("toolUseId"))
                normalized_id = tool_id if isinstance(tool_id, str) else None
                result_event = _event(record, kind="result", text=_text(block.get("content")), line_number=line_number, block_index=index, tool_use_id=normalized_id)
                segments = compound_calls.get(normalized_id) if normalized_id is not None else None
                if segments is None:
                    result.append(result_event)
                    continue
                outputs = helper_segment_results(segments, result_event["text"], block.get("is_error"))
                for segment in segments:
                    if segment["index"] in outputs:
                        segment_result = _segment_event(result_event, segment, outputs[segment["index"]]["output"])
                        if outputs[segment["index"]]["exit_code"] is not None:
                            segment_result["exit_code"] = outputs[segment["index"]]["exit_code"]
                        result.append(segment_result)
        return result
    if isinstance(content, str):
        return [_event(record, kind="response", text=content, line_number=line_number)] if content else []
    if not isinstance(content, list):
        return []
    actions = []
    for index, block in enumerate(content):
        if isinstance(block, dict) and block.get("type") == "tool_use":
            tool_id = block.get("id")
            normalized_id = tool_id if isinstance(tool_id, str) else None
            action_event = _event(record, kind="action", text=_text(block.get("input")), line_number=line_number, block_index=index, tool_use_id=normalized_id)
            if isinstance(normalized_id, str):
                action_event["tool_name"] = block.get("name")
                action_event["input"] = block.get("input", block.get("arguments", block.get("args")))
            segments = compound_calls.get(normalized_id) if normalized_id is not None else None
            if segments is None:
                actions.append(action_event)
                continue
            for segment in segments:
                segment_action = _segment_event(action_event, segment, segment["text"])
                segment_action["input"] = {"command": segment["text"]}
                if segment["kind"] == "write":
                    segment_action["action_kind"] = "edit"
                    segment_action["target"] = segment["path"]
                else:
                    # The frozen helper checks the workload file; the projection assigns the same target.
                    segment_action["target"] = WORKLOAD_TARGET
                actions.append(segment_action)
    return actions or ([_event(record, kind="response", text=_text(content), line_number=line_number)] if _text(content) else [])


def decode_claude_code_bundle(package: Path) -> dict[str, Any]:
    """Decode messages represented by the declared copied JSONL file only."""

    session_bytes = _validate_package(package)
    events: list[dict[str, Any]] = []
    session_ids: set[str] = set()
    parsed_rows: list[tuple[int, dict[str, Any]]] = []
    for line_number, line in enumerate(session_bytes.decode("utf-8").splitlines(), 1):
        row = _strict_json(line, f"session.jsonl:{line_number}")
        if not isinstance(row, dict):
            raise ClaudeCodeDecoderError(f"session.jsonl:{line_number}: record must be an object")
        session_id = row.get("sessionId")
        if session_id is not None and (not isinstance(session_id, str) or not session_id):
            raise ClaudeCodeDecoderError(f"session.jsonl:{line_number}: sessionId is missing")
        if isinstance(session_id, str):
            session_ids.add(session_id)
        parsed_rows.append((line_number, row))
    compound_calls: dict[str, list[dict[str, Any]]] = {}
    for _line_number, row in parsed_rows:
        if row.get("type") != "assistant":
            continue
        message = row.get("message")
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_use" and isinstance(block.get("id"), str) and block["id"]:
                segments = compound_shell_segments(block.get("name"), block.get("input", block.get("arguments", block.get("args"))))
                if segments is not None:
                    compound_calls[block["id"]] = segments
    for line_number, row in parsed_rows:
        record_type = row.get("type")
        if record_type not in {"user", "assistant"}:
            continue
        row_session_id = row.get("sessionId")
        message = row.get("message")
        if not isinstance(message, dict) or message.get("role") != record_type or not isinstance(row_session_id, str):
            raise ClaudeCodeDecoderError(f"session.jsonl:{line_number}: message role or session ID is invalid")
        events.extend(_events_for_record(row, message, line_number, compound_calls))
    if len(session_ids) != 1:
        raise ClaudeCodeDecoderError("copied session must contain exactly one session ID")
    return {
        "format": FORMAT,
        "package": {
            "format": FORMAT,
            "artifacts": [{"id": "session", "path": SESSION_NAME, "sha256": hashlib.sha256(session_bytes).hexdigest(), "size_bytes": len(session_bytes)}],
        },
        "session_id": next(iter(session_ids)),
        "events": events,
        "counts": {
            "submitted_turns": sum(event["kind"] == "submitted_turn" for event in events),
            "responses": sum(event["kind"] == "response" for event in events),
            "actions": sum(event["kind"] == "action" for event in events),
            "results": sum(event["kind"] == "result" for event in events),
        },
    }
