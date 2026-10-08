"""Synthetic tests: no harness, credentials, native-store discovery or live data."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from session_bench.pi_score_inputs import (
    CAPTURE_SCHEMA, UNOBSERVED, PRINT_UNOBSERVED, observer_from_capture_documents,
    qualify_capture_documents, project_pi_native, compare_pi_capture,
    build_pi_format_evidence, sha,
)
from session_bench.pi_session_stats import REQUEST_ID, validate_pi_session_stats
from session_bench.score_replay import build_score_replay_package, canonical, replay_score_package
from session_bench.v1_public_score import validate_format_profile


def packed(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n").encode()


def fixture(json_stdout=False):
    run, session = "pi-synthetic", "synthetic-session"
    capture_environment = {"schema_version": "session-bench-pi-capture-host-v1",
                          "os_name": "macOS", "system": "Darwin", "python_platform": "darwin"}
    canary = "SB_SURVIVAL_V1_RUN_" + run
    before, after, helper = b"old checkout\n", b"actual edited checkout\n", b"synthetic helper source\n"
    turns = [{"id": "turn-r"+str(n), "revision": "r"+str(n),
              "text": ("Requirement R1" if n == 1 else "Correction R2") + " " + canary,
              "response_canary": "SB_SURVIVAL_V1_RESPONSE_R"+str(n)+"_unicode_Δ"} for n in (1, 2)]
    workload = {"schema_version": "1.0-survival-workload", "run_id": run, "run_canary": canary, "turns": turns,
                "actions": [
                    {"id": "action-inspect", "turn_id": "turn-r1", "kind": "inspect",
                     "argv": ["python3", "bench_check.py", "inspect"], "target": "fixture_project/checkout.py",
                     "helper_nonce": "nonce-inspect"},
                    {"id": "action-baseline", "turn_id": "turn-r1", "kind": "test",
                     "argv": ["python3", "bench_check.py", "baseline"], "target": "fixture_project/checkout.py",
                     "helper_nonce": "nonce-baseline"},
                    {"id": "action-edit", "turn_id": "turn-r2", "kind": "edit",
                     "argv": ["replace_function", "fixture_project/checkout.py"], "target": "fixture_project/checkout.py"},
                    {"id": "action-final", "turn_id": "turn-r2", "kind": "test",
                     "argv": ["python3", "bench_check.py", "final"], "target": "fixture_project/checkout.py",
                     "helper_nonce": "nonce-final"},
                ],
                "helper": {"sha256": sha(helper), "nonces": {p: "nonce-"+p for p in ("inspect", "baseline", "final")}}}
    plan = {"schema_version": CAPTURE_SCHEMA, "attempt_id": run, "configuration_id": "pi",
            "session_id": session, "repetition": 1, "model": "synthetic-model", "provider": "synthetic-provider",
            "workspace": "/synthetic/workspace", "session_dir": "/synthetic/pi-session-root",
            "capture_environment": capture_environment,
            "workload_sha256": sha(b"{}\n"),
            "protected_sha256": {"checkout.py": sha(before), "bench_check.py": sha(helper)}}
    documents = {"plan.json": packed(plan), "workload-instance.json": packed(workload),
                 "workload-template.json": b"{}\n", "workspaces/before/fixture_project/checkout.py": before}
    ledger = []
    for phase in ("inspect", "baseline", "final"):
        payload = {"phase": phase}
        if phase == "inspect":
            # The real helper prints the checkout source with its own digest.
            payload.update(checkout_sha256=sha(before), checkout_source=before.decode())
        if phase == "final":
            payload["tests"] = [{"passed": True, "actual": n, "expected": n} for n in range(3)]
        nonce = "nonce-"+phase
        ledger.append({"schema_version": "1.0-survival-helper-ledger", "phase": phase,
                       "helper_nonce": nonce, "id": f"helper-{phase}-{nonce}", "run_canary": canary,
                       "argv": ["python3", "bench_check.py", phase], "cwd": "fixture_project",
                       "exit_code": 1 if phase == "baseline" else 0,
                       "checkout_sha256": sha(after if phase == "final" else before),
                       "output": f"SB_SURVIVAL_V1_HELPER_{phase.upper()}_{nonce} " + json.dumps(payload)})
    native = [{"type": "session", "version": 3, "id": session}]
    parent = None
    def row(identifier, role, content, **extra):
        nonlocal parent
        native.append({"type": "message", "id": identifier, "parentId": parent,
                       "message": {"role": role, "content": content, **extra}})
        parent = identifier
    captures = []
    for n, turn in enumerate(turns, 1):
        row(f"user{n}", "user", [{"type": "text", "text": turn["text"]}])
        if n == 2 and json_stdout:
            for tool in ("read", "edit"):
                arguments = {"path": "/synthetic/workspace/fixture_project/checkout.py"}
                if tool == "edit":
                    arguments["edits"] = [{"oldText": before.decode(), "newText": after.decode()}]
                row(tool+"call", "assistant", [{"type": "toolCall", "id": tool, "name": tool,
                    "arguments": arguments}], stopReason="toolUse", responseId=tool+"response")
                row(tool+"result", "toolResult", [{"type": "text", "text": "observed "+tool}],
                    toolCallId=tool, toolName=tool, isError=False)
        command = f"cd fixture_project && python3 bench_check.py {'inspect' if n==1 else 'final'} --run-canary {canary}"
        if n == 1:
            command += f" && python3 bench_check.py baseline --run-canary {canary}"
        row(f"callrow{n}", "assistant", [{"type": "text", "text": "tool preamble, not final"},
            {"type": "toolCall", "id": f"call{n}", "name": "bash", "arguments": {"command": command}}], stopReason="toolUse", responseId=f"toolresponse{n}")
        output = "\n".join(item["output"] for item in (ledger[:2] if n == 1 else ledger[2:])) + "\n"
        row(f"resultrow{n}", "toolResult", [{"type": "text", "text": output}],
            toolCallId=f"call{n}", toolName="bash", isError=n == 1)
        final = f"Actual response {n}. " + turn["response_canary"]
        row(f"response{n}", "assistant", [{"type": "text", "text": final}], stopReason="stop",
            model=plan["model"], provider=plan["provider"], responseId=f"finalresponse{n}", usage={"input": 42, "output": 3, "cacheRead": 0, "cacheWrite": 0, "reasoning": 0, "totalTokens": 45})
        raw = b"".join(packed(item) for item in native)
        prefix = f"turn-r{n}"
        stdout = (final+"\n").encode()
        if json_stdout:
            messages = native[next(i for i, item in enumerate(native) if item.get("id") == f"user{n}"):]
            stream = [{"type": "session", "version": 3, "id": session, "cwd": plan["workspace"]}, {"type": "agent_start"}]
            for item in messages:
                message = item["message"]
                if message["role"] == "toolResult":
                    stream.append({"type": "tool_execution_end", "toolCallId": message["toolCallId"],
                        "toolName": message["toolName"], "result": {"content": message["content"]}, "isError": message["isError"]})
                stream.append({"type": "message_end", "message": message})
                for block in message["content"]:
                    if block.get("type") == "toolCall":
                        stream.append({"type": "tool_execution_start", "toolCallId": block["id"],
                            "toolName": block["name"], "args": block["arguments"]})
            stream += [{"type": "message_update", "usage": {"input": 999999}}, {"type": "agent_end"}, {"type": "agent_settled"}]
            stdout = b"".join(packed(item) for item in stream)
        launch = {"session_id": session, "model": plan["model"], "provider": plan["provider"],
                  "cwd": plan["workspace"], "prompt_sha256": sha(turn["text"].encode()),
                  "argv": ["pi", "--session-dir", plan["session_dir"],
                           "--session-id", session, "--provider", plan["provider"],
                           "--model", plan["model"], "--", turn["text"]]}
        if json_stdout:
            launch["argv"][1:1] = ["--mode", "json"]
        receipt = {"turn": n, "launch": launch, "returncode": 0, "status": "completed",
                   "stdout_sha256": sha(stdout), "stderr_sha256": sha(b""), "native_sha256": sha(raw)}
        captures.append(receipt)
        documents.update({prefix+"/launch.json": packed(launch),
                          prefix+"/exit.json": packed({k: receipt[k] for k in ("returncode", "stdout_sha256", "stderr_sha256")}),
                          prefix+"/stdout.txt": stdout, prefix+"/stderr.txt": b"",
                          prefix+"/native/session.jsonl": raw,
                          prefix+"/native/receipt.json": packed({"sha256": sha(raw), "size_bytes": len(raw),
                              "relative_path": f"2026-10-01T00-00-00-000Z_{session}.jsonl"}),
                          f"observer/prompt-r{n}.txt": turn["text"].encode(),
                          prefix+"/workspace/fixture_project/checkout.py": before if n == 1 else after,
                          prefix+"/workspace/fixture_project/bench_check.py": helper,
                          prefix+"/workspace/fixture_project/snapshots/checkout.after.py": b"reference, not actual\n",
                          prefix+"/workspace/fixture_project/.survival-observer.jsonl": b"".join(packed(item) for item in (ledger[:2] if n == 1 else ledger))})
    documents["capture-result.json"] = packed({"schema_version": CAPTURE_SCHEMA, "attempt_id": run,
        "session_id": session, "status": "captured_pending_qualification", "model_submissions": 2,
        "model": plan["model"], "provider": plan["provider"], "capture_environment": capture_environment,
        "turns": captures})
    return documents


def add_synthetic_pi_stats(documents, input_tokens=84):
    session = documents["turn-r2/native/session.jsonl"]
    header = json.loads(session.splitlines()[0])
    filename = "2026-10-01T00-00-00-000Z_" + header["id"] + ".jsonl"
    tokens = {"input": input_tokens, "output": 6, "cacheRead": 0,
              "cacheWrite": 0, "total": input_tokens + 6}
    response = {"type": "response", "id": REQUEST_ID,
                "command": "get_session_stats", "success": True,
                "data": {"sessionFile": "/tmp/copy/" + filename,
                         "sessionId": header["id"], "userMessages": 2,
                         "assistantMessages": 8, "toolCalls": 4,
                         "toolResults": 4, "totalMessages": 14,
                         "tokens": tokens, "cost": 0.01,
                         "contextUsage": {"tokens": 1, "contextWindow": 1000,
                                          "percent": 0.1}}}
    stdout = (json.dumps(response) + "\n").encode()
    receipt = validate_pi_session_stats(stdout, session_bytes=session,
                                        session_filename=filename, pi_version="1.0.0")
    receipt.update({"attempt_id": "pi-synthetic", "captured_at": "2026-10-01T00:00:00Z",
                    "rpc_stdout_sha256": sha(stdout), "rpc_stderr_sha256": sha(b""),
                    "network_disabled": True, "user_config_disabled": True,
                    "model_submissions": 0})
    documents["observer/pi-session-stats.json"] = packed(receipt)
    documents["observer/pi-session-stats.stdout.jsonl"] = stdout
    documents["observer/pi-session-stats.stderr.txt"] = b""
    return documents


class PiScoreInputsTests(unittest.TestCase):
    def test_capture_os_identity_is_pinned_and_consistent(self):
        docs = fixture()
        identity = qualify_capture_documents(docs)
        self.assertEqual(identity["capture_environment"]["os_name"], "macOS")
        result = json.loads(docs["capture-result.json"])
        result["capture_environment"]["os_name"] = "Linux"
        docs["capture-result.json"] = packed(result)
        with self.assertRaisesRegex(ValueError, "operating-system identity is missing or inconsistent"):
            qualify_capture_documents(docs)

    def test_independent_observer_never_uses_native_or_reference_after(self):
        docs = fixture()
        expected = observer_from_capture_documents(docs)
        docs["turn-r2/native/session.jsonl"] = b"not native JSON"
        docs["turn-r2/workspace/fixture_project/snapshots/checkout.after.py"] = b"irrelevant reference"
        self.assertEqual(expected, observer_from_capture_documents(docs))
        changes = [event for event in expected["events"] if event["kind"] == "file_change"]
        self.assertEqual(changes[0]["fields"]["after_sha256"], sha(b"actual edited checkout\n"))
        self.assertEqual(len([e for e in expected["events"] if e["kind"] == "action"]), 3)

    def test_native_ancestry_and_compound_population(self):
        native = project_pi_native(fixture()["turn-r2/native/session.jsonl"])
        self.assertEqual([r["turn_id"] for r in native["responses"]], ["user1", "user2"])
        self.assertEqual(len(native["responses"]), 2)
        self.assertEqual(len(native["actions"]), 2)  # compound R1 remains one call
        self.assertNotIn("helper_phase", native["actions"][0])
        self.assertIn("bench_check.py final", native["actions"][1]["argv"][-1])
        self.assertNotIn("exit_code", native["results"][1])
        self.assertEqual(native["file_changes"], [])
        self.assertEqual(len(native["usage"]), 2)
        self.assertEqual(native["reconciliation"], [])

    def test_integrity_rejects_corruption_and_native_mutation(self):
        for path in ("observer/prompt-r1.txt", "turn-r2/stdout.txt", "turn-r1/workspace/fixture_project/checkout.py",
                     "turn-r2/workspace/fixture_project/bench_check.py", "turn-r2/native/session.jsonl"):
            with self.subTest(path=path):
                docs = fixture()
                docs[path] += b"tampered"
                with self.assertRaises(ValueError):
                    qualify_capture_documents(docs)

    def test_launch_identity_and_helper_provenance_rejected(self):
        for field, replacement in (("run_canary", "other-run"), ("helper_nonce", "other-nonce"),
                                   ("checkout_sha256", "0"*64), ("exit_code", True)):
            docs = fixture()
            path = "turn-r2/workspace/fixture_project/.survival-observer.jsonl"
            rows = [json.loads(line) for line in docs[path].splitlines()]
            rows[-1][field] = replacement
            docs[path] = b"".join(packed(row) for row in rows)
            with self.subTest(field=field), self.assertRaises(ValueError):
                observer_from_capture_documents(docs)
        docs = fixture()
        launch = json.loads(docs["turn-r2/launch.json"])
        launch["session_id"] = "another-session"
        docs["turn-r2/launch.json"] = packed(launch)
        with self.assertRaises(ValueError):
            observer_from_capture_documents(docs)

    def test_dangling_parent_duplicate_call_unknown_version_fail_closed(self):
        for mutation in ("parent", "call", "version"):
            rows = [json.loads(line) for line in fixture()["turn-r2/native/session.jsonl"].splitlines()]
            if mutation == "parent":
                rows[-1]["parentId"] = "missing"
            elif mutation == "call":
                rows[6]["message"]["content"][1]["id"] = "call1"
            else:
                rows[0]["version"] = 99
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                project_pi_native(b"".join(packed(row) for row in rows))

    def test_unresolved_usage_root_and_selected_loss(self):
        docs = fixture()
        intact = compare_pi_capture(docs)
        rows = {row["id"]: row for row in intact["metrics"]}
        for metric in UNOBSERVED + PRINT_UNOBSERVED:
            self.assertEqual(rows[metric]["state"], "unresolved")
        self.assertFalse(any(row["state"] == "native_absent" for row in rows.values()))
        self.assertEqual(rows["work.visible_responses"]["correct"], 2)
        self.assertEqual(rows["work.actions"]["correct"], 0)  # print-only observer lacks full action population
        self.assertEqual(rows["work.results"]["correct"], 0)  # no numeric native exits
        damaged = b"\n".join(docs["turn-r2/native/session.jsonl"].splitlines()[:-1]) + b"\n"
        after = {row["id"]: row for row in compare_pi_capture(docs, native_raw=damaged)["metrics"]}
        self.assertEqual(after["work.visible_responses"]["observed_eligible"], 2)
        self.assertEqual(after["work.visible_responses"]["correct"], 1)

    def test_json_stdout_final_usage_and_exact_tool_population(self):
        docs = fixture(json_stdout=True)
        observer = observer_from_capture_documents(docs)
        actions = [event for event in observer["events"] if event["kind"] == "action"]
        responses = [event for event in observer["events"] if event["kind"] == "assistant_response"]
        self.assertEqual(len(actions), 4)
        self.assertEqual(len([a for a in actions if a["fields"]["turn_id"] == "turn-r1"]), 1)
        self.assertEqual([r["fields"]["usage"]["input_tokens"] for r in responses], [42, 42])
        rows = {row["id"]: row for row in compare_pi_capture(docs)["metrics"]}
        for name, correct in (("work.actions", 4), ("work.results", 4), ("work.visible_responses", 2),
                              ("attribution.usage", 2), ("attribution.token_semantics", 2)):
            self.assertEqual(rows[name]["correct"], correct, name)
        self.assertEqual(rows["attribution.reconciliation"]["state"], "unresolved")
        self.assertEqual(rows["portable.complete_root"]["state"], "unresolved")
        self.assertEqual(rows["work.changed_files"]["state"], "measured")
        self.assertEqual(rows["work.changed_files"]["correct"], 1)
        self.assertEqual(rows["revision.final_after_r2"]["state"], "measured")

    def test_stdout_usage_total_includes_completed_tool_use_model_steps(self):
        docs = fixture(json_stdout=True)
        stream_path = "turn-r2/stdout.txt"
        rows = [json.loads(line) for line in docs[stream_path].splitlines()]
        tool_step = next(row["message"] for row in rows
                         if row.get("type") == "message_end"
                         and isinstance(row.get("message"), dict)
                         and row["message"].get("stopReason") == "toolUse")
        tool_step["usage"] = {"input": 6, "output": 5, "cacheRead": 0,
                              "cacheWrite": 0, "reasoning": 1, "totalTokens": 11}
        raw = b"".join(packed(row) for row in rows)
        docs[stream_path] = raw
        exit_path = "turn-r2/exit.json"
        exit_receipt = json.loads(docs[exit_path])
        exit_receipt["stdout_sha256"] = sha(raw)
        docs[exit_path] = packed(exit_receipt)
        result = json.loads(docs["capture-result.json"])
        result["turns"][1]["stdout_sha256"] = sha(raw)
        docs["capture-result.json"] = packed(result)

        observer = observer_from_capture_documents(docs)
        totals = [event["fields"] for event in observer["events"]
                  if event["kind"] == "usage_total"]
        self.assertEqual(totals, [{"input_tokens": 90, "output_tokens": 11,
                                   "cache_read_tokens": 0, "cache_write_tokens": 0}])

    def test_changed_file_pre_image_comes_from_the_native_inspect_output(self):
        # A wrong caller-supplied snapshot must not reach the native fact.
        docs = fixture(json_stdout=True)
        source = "def checkout(items):\n    return sum(price for price, quantity in items) + 5\n"
        inspect = json.dumps({"checkout_sha256": sha(source.encode()), "checkout_source": source, "phase": "inspect"})
        rows = [json.loads(line) for line in docs["turn-r2/native/session.jsonl"].splitlines()]
        for row in rows:
            message = row.get("message", {})
            if message.get("role") == "toolResult" and message.get("toolCallId") == "call1":
                message["content"] = [{"type": "text", "text": "SB_SURVIVAL_V1_HELPER_INSPECT_nonce-inspect " + inspect}]
            for block in message.get("content", []) if message.get("role") == "assistant" else []:
                if block.get("type") == "toolCall" and block.get("name") == "edit":
                    block["arguments"]["edits"] = [{"oldText": "price for price, quantity in items) + 5", "newText": "price * quantity for price, quantity in items)"}]
        native = project_pi_native(b"".join(packed(row) for row in rows), before_checkout=b"not the real file\n",
                                   after_checkout=b"ignored\n", workspace=json.loads(docs["plan.json"])["workspace"])
        after = "def checkout(items):\n    return sum(price * quantity for price, quantity in items)\n"
        self.assertEqual(native["file_changes"][0]["before_sha256"], sha(source.encode()))
        self.assertEqual(native["file_changes"][0]["after_sha256"], sha(after.encode()))

    def test_no_changed_file_fact_without_a_native_pre_image(self):
        docs = fixture(json_stdout=True)
        rows = [json.loads(line) for line in docs["turn-r2/native/session.jsonl"].splitlines()]
        for row in rows:
            message = row.get("message", {})
            if message.get("role") == "toolResult" and message.get("toolCallId") == "call1":
                message["content"] = [{"type": "text", "text": "SB_SURVIVAL_V1_HELPER_INSPECT_nonce-inspect {\"phase\": \"inspect\"}"}]
        native = project_pi_native(b"".join(packed(row) for row in rows),
                                   before_checkout=docs["workspaces/before/fixture_project/checkout.py"],
                                   after_checkout=docs["turn-r2/workspace/fixture_project/checkout.py"],
                                   workspace=json.loads(docs["plan.json"])["workspace"])
        self.assertEqual(native["file_changes"], [])

    def test_rpc_session_stats_are_not_a_native_session_total(self):
        # Pi's session file declares no session total. An RPC stats receipt is
        # harness output, not native bytes, so it cannot reconcile the record.
        docs = add_synthetic_pi_stats(fixture(json_stdout=True), 84)
        rows = {row["id"]: row for row in compare_pi_capture(docs)["metrics"]}
        self.assertEqual(rows["attribution.reconciliation"]["decoded_eligible"], 0)
        self.assertEqual(rows["attribution.reconciliation"]["correct"], 0)

    def test_json_usage_mismatch_and_selected_response_loss(self):
        docs = fixture(json_stdout=True)
        rows = [json.loads(line) for line in docs["turn-r2/native/session.jsonl"].splitlines()]
        rows[-1]["message"]["usage"]["input"] += 1
        changed = {row["id"]: row for row in compare_pi_capture(docs, native_raw=b"".join(packed(row) for row in rows))["metrics"]}
        # Usage is native-attested: a differing token value keeps the join.
        self.assertEqual(changed["attribution.usage"]["correct"], 2)
        self.assertEqual(changed["attribution.usage"]["decoded_eligible"], 2)
        lost = {row["id"]: row for row in compare_pi_capture(docs, native_raw=b"".join(packed(row) for row in rows[:-1]))["metrics"]}
        self.assertEqual(lost["work.visible_responses"]["observed_eligible"], 2)
        self.assertEqual(lost["work.visible_responses"]["correct"], 1)
        self.assertEqual(lost["attribution.usage"]["decoded_eligible"], 1)

    def test_json_stdout_rejects_duplicate_finals_and_execution_mismatch(self):
        from session_bench.pi_score_inputs import parse_pi_json_stdout
        docs = fixture(json_stdout=True)
        args = {"session_id": "synthetic-session", "workspace": "/synthetic/workspace",
                "prompt": json.loads(docs["workload-instance.json"])["turns"][0]["text"],
                "response_canary": "SB_SURVIVAL_V1_RESPONSE_R1_unicode_Δ"}
        original = [json.loads(line) for line in docs["turn-r1/stdout.txt"].splitlines()]
        for change in ("duplicate", "prompt", "execution", "header", "usage"):
            rows = copy.deepcopy(original)
            if change == "duplicate":
                rows.insert(-2, next(row for row in rows if row["type"] == "message_end" and row["message"].get("stopReason") == "stop"))
            elif change == "prompt":
                next(row for row in rows if row["type"] == "message_end" and row["message"]["role"] == "user")["message"]["content"][0]["text"] = "wrong prompt"
            elif change == "execution":
                next(row for row in rows if row["type"] == "tool_execution_start")["args"] = {}
            elif change == "usage":
                next(row for row in rows if row["type"] == "message_end" and row["message"].get("stopReason") == "stop")["message"]["usage"]["input"] = True
            else:
                rows[0]["id"] = "wrong session"
            with self.subTest(change=change), self.assertRaises(ValueError):
                parse_pi_json_stdout(b"".join(packed(row) for row in rows), **args)

    def test_format_profile_has_twelve_rows_and_frozen_response_denominator(self):
        docs = fixture()
        observer = observer_from_capture_documents(docs)
        context = {"run_id": "pi-synthetic", "configuration_id": "pi", "repetition": 1,
                   "build": "synthetic-build", "collected_on": "2026-10-01", "result_id": "synthetic-result"}
        common = {"observer_document": packed(observer), "observer": {"id": "observer", "sha256": sha(packed(observer))},
                  "native_manifest": {"id": "native", "sha256": "a"*64},
                  "pi_root_evidence_locators": [
                      {"id": "inputs/pi-root-repetitions.json", "sha256": "b"*64},
                      {"id": "inputs/capture/native-root/root-capture.json", "sha256": "c"*64},
                  ]}
        original = project_pi_native(docs["turn-r2/native/session.jsonl"])
        format_evidence = build_pi_format_evidence(original, context=context, common=common)
        profile = format_evidence["profile"]
        metrics = validate_format_profile(profile)["metrics"]
        self.assertEqual(len(metrics), 12)
        self.assertEqual(sum(row["state"] == "measured" for row in metrics), 4)
        stable_root = next(row for row in format_evidence["metric_evidence"]
                           if row["metric_id"] == "broad.stable_root_location")
        self.assertEqual(stable_root["observer_ids"], ["pi-root-repetition-capture"])
        self.assertEqual(stable_root["native_locators"], common["pi_root_evidence_locators"])
        damaged = copy.deepcopy(original)
        damaged["responses"].pop()
        other = build_pi_format_evidence(damaged, context=context, common=common)["profile"]
        self.assertEqual(profile["broad_evidence"]["broad.readable_rationale"]["response_ids"],
                         other["broad_evidence"]["broad.readable_rationale"]["response_ids"])
        self.assertEqual(len(other["broad_evidence"]["broad.readable_rationale"]["records"]), 1)

    def test_closed_private_score_packet_replays_usage_and_selected_loss(self):
        docs = add_synthetic_pi_stats(fixture(json_stdout=True), 84)
        identity = qualify_capture_documents(docs)
        observer = canonical(observer_from_capture_documents(docs))
        native_bytes = docs["turn-r2/native/session.jsonl"]
        native_inventory = {"format": "pi-native-session-jsonl-v3", "artifacts": [{
            "id": "pi-session", "path": "session.jsonl", "sha256": sha(native_bytes),
            "size_bytes": len(native_bytes), "depends_on": [],
        }]}
        assertion = {"schema_version": "session-bench-pi-score-capture-v1",
                     "attempt_id": identity["run_id"], "session_id": identity["session_id"],
                     "repetition": identity["repetition"],
                     "capture_environment": identity["capture_environment"],
                     "capture_documents": [{"path": name, "sha256": sha(raw), "size_bytes": len(raw)}
                                           for name, raw in sorted(docs.items())]}
        context = {"schema_version": "session-bench-native-score-replay-v1", "configuration_id": "pi",
                   "repetition": 1, "run_id": identity["run_id"], "build": "pi test", "collected_on": "2026-10-01",
                   "result_id": "pi-synthetic-result", "observer_kind": "pi-json-capture-v1",
                   "complete_record_family": False, "complete_root": False, "required_companions": [],
                   "root_repetitions": None, "capture_assertion_path": "inputs/pi-capture-assertion.json",
                   "claude_projection": None}
        supporting = {"pi-capture-assertion.json": canonical(assertion)}
        supporting.update({"capture/" + name: raw for name, raw in docs.items()})
        temp_parent = "/private/tmp" if Path("/private/tmp").is_dir() else None
        with tempfile.TemporaryDirectory(prefix="pi-replay-test-", dir=temp_parent) as temporary:
            root = Path(temporary)
            native = root / "native"
            native.mkdir()
            (native / "decode.json").write_bytes(canonical(native_inventory) + b"\n")
            (native / "session.jsonl").write_bytes(native_bytes)
            package = root / "package"
            build_score_replay_package(native, package, workload_document=docs["workload-instance.json"],
                observer_document=observer, context_document=canonical(context), supporting_documents=supporting)
            manifest_sha = hashlib.sha256((package / "manifest.json").read_bytes()).hexdigest()
            receipt = replay_score_package(package, expected_manifest_sha256=manifest_sha)
        intact = receipt["diagnostics"]["intact"]
        metric_rows = {row["id"]: row for row in intact["metrics"]}
        self.assertEqual(metric_rows["attribution.usage"]["state"], "measured")
        self.assertEqual(metric_rows["attribution.token_semantics"]["state"], "measured")
        # No native session total, and this packet's root boundary is incomplete.
        self.assertEqual(metric_rows["attribution.reconciliation"]["state"], "unresolved")
        self.assertEqual(metric_rows["work.changed_files"]["state"], "measured")
        self.assertEqual(metric_rows["portable.complete_root"]["state"], "unresolved")
        self.assertFalse(intact["score_diagnostics"]["rankable"])
        self.assertTrue(receipt["diagnostics"]["selected_loss"]["observer_denominator_unchanged"])
        self.assertTrue(receipt["diagnostics"]["selected_loss"]["response_correctness_reduced"])


if __name__ == "__main__":
    unittest.main()
