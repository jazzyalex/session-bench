"""Every 64-hex digest in a public set is public bytes, replay output or allowlisted with a reason."""
import hashlib
import json
import re

import pytest

from session_bench.public_digest_check import (
    UnboundDigestError, benchmark_public_references, classify_public_digests, public_entry_names, read_public_set,
    require_bound_public_digests,
)

sha = lambda data: hashlib.sha256(data).hexdigest()
canonical = lambda value: json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
REASON = "digest over a metadata inventory of thousands of private files"


def public_set():
    transcript = b'{"type":"user","text":"hello"}\n{"type":"assistant","text":"done"}\n'
    row = {"id": 7, "text": "a row"}
    return {
        "run-1/native/session.jsonl": transcript,
        "run-1/native/decode.json": canonical({"artifacts": [{"path": "session.jsonl", "sha256": sha(transcript)}]}),
        "run-1/inputs/locators.json": canonical({"line": sha(b'{"type":"user","text":"hello"}'), "document": sha(canonical(row)),
                                                 "member": sha(canonical({"type": "assistant", "text": "done"})), "empty": sha(b""), "zero": "0" * 64}),
        "run-1/inputs/row.json": json.dumps({"rows": [row]}, indent=2).encode(),
        "run-2/inputs/other.json": canonical({"other_packet_file": sha(transcript)}),
    }


def test_digests_of_public_bytes_of_any_packet_of_the_set_are_class_one():
    result = classify_public_digests(public_set(), public_references={})
    assert result["unbound"] == [] and result["classes"] == {"public_bytes": 7, "replay_output": 0, "allowlisted": 0}


def test_a_digest_of_bytes_outside_the_set_is_unbound_and_fails():
    contents = public_set()
    private = sha(b'{"type":"user","text":"hello from a private e-mail"}')
    contents["run-1/inputs/attempt.json"] = canonical({"evidence": {"sha256": private}})
    result = classify_public_digests(contents, public_references={})
    assert result["unbound"] == [{"file": "run-1/inputs/attempt.json", "digest": private}]
    with pytest.raises(UnboundDigestError, match="attempt.json"):
        require_bound_public_digests(contents, public_references={})


def test_replay_output_and_its_member_documents_are_class_two():
    contents = public_set()
    metrics = [{"id": "m", "correct": 1}]
    receipt = canonical({"diagnostics_sha256": "ab" * 32, "diagnostics": {"intact": {"metrics": metrics}}})
    contents["run-1/manifest.json"] = canonical({"expected_diagnostics_sha256": "ab" * 32, "metric_rows_sha256": sha(canonical(metrics))})
    assert classify_public_digests(contents, public_references={})["unbound"] != []
    result = require_bound_public_digests(contents, replay_outputs=[receipt], public_references={})
    assert result["classes"]["replay_output"] == 2


def test_allowlist_names_the_field_and_needs_a_written_reason():
    contents = public_set()
    contents["run-1/inputs/root/inventory.json"] = canonical({"metadata_digest": "cd" * 32, "other": "ef" * 32})
    allow = {"inventory.json": {"metadata_digest": REASON}}
    result = classify_public_digests(contents, allowlist=allow, public_references={})
    assert result["classes"]["allowlisted"] == 1 and [item["digest"] for item in result["unbound"]] == ["ef" * 32]
    with pytest.raises(ValueError, match="written reason"):
        classify_public_digests(contents, allowlist={"inventory.json": {"metadata_digest": "ok"}}, public_references={})
    nested = {"inventory.json": {"*": REASON}}
    assert classify_public_digests(contents, allowlist=nested, public_references={})["unbound"] == []


def test_benchmark_fixture_and_observed_workload_file_digests_are_bound():
    references = benchmark_public_references()
    helper = sha(references["benchmark-fixture/fixture_project/bench_check.py"])
    edited = sha(b"def checkout(items):\n    return 1\n")
    contents = {"run-1/inputs/workload.json": canonical({"helper": {"sha256": helper}}),
                "run-1/native/session.jsonl": canonical({"text": "shasum says " + edited}) + b"\n"}
    assert [item["digest"] for item in classify_public_digests(contents)["unbound"]] == [edited]
    contents["run-1/inputs/observer.json"] = canonical({"events": [{"kind": "file_change", "fields": {"after_sha256": edited}}]})
    assert classify_public_digests(contents)["unbound"] == []


# --- every current public set, with the allowlist of its own sanitizer ---
import importlib.util
from pathlib import Path

from session_bench.public_digest_check import check_public_packets

ROOT = Path(__file__).resolve().parents[1]
PREPARATION = ROOT / "artifacts/v1-expanded-preparation"
# set directory, packet directory inside it, sanitizer script, receipt pattern
PUBLIC_SETS = {
    "claude-cli": ("claude-cli-public-candidates-v9", "", "sanitize_claude_score_packets", "{run}-receipt.json"),
    "claude-desktop": ("claude-desktop-public-candidates-v3", "", "sanitize_claude_desktop_score_packets", "{run}-receipt.json"),
    "codex-cli": ("codex-cli-public-candidates-v8", "", "sanitize_codex_score_packets", "{run}-receipt.json"),
    "copilot": ("copilot-public-candidates-v10", "", "sanitize_copilot_score_packets", "{run}-receipt.json"),
    "deepseek-harness-cli": ("dsh-public-score-preparation-v13", "public-candidates", "build_dsh_public_replays", "receipts/{run}.candidate.json"),
    "opencode-cli": ("opencode-1.18.31-public-candidates-v8", "", "sanitize_opencode_score_packets", "{run}.producer-replay.json"),
    "pi": ("pi-public-score-preparation-v7", "public-candidates", "build_pi_public_replays", "../pi-independent-public-review-v7/{run}.replay.json"),
    "antigravity": ("antigravity-public-candidates-v4", "", "sanitize_antigravity_score_packets", "{run}-receipt.json"),
    "cursor-cli": ("cursor-cli-public-candidates-v3", "", "sanitize_cursor_cli_score_packets", "{run}-receipt.json"),
    "hermes": ("hermes-public-candidates-v3", "", "sanitize_hermes_score_packets", "{run}-receipt.json"),
    "openclaw": ("openclaw-public-candidates-v6", "", "sanitize_openclaw_score_packets", "{run}-receipt.json"),
    "kimi": ("kimi-public-candidates-v4", "", "sanitize_kimi_score_packets", "{run}-receipt.json"),
}
# Hex runs of 12 or more characters that are not 64-hex digests (``long_hex_runs``): every set has none unlisted.
# The sets use the written, proved rules of ``CONFIGURATION_HEX_ALLOWLIST`` in the module and the reviewed provider id
# fields of ``PROVIDER_ID_FIELDS``.


def _sanitizer(name):
    import sys
    if str(ROOT / "scripts") not in sys.path:
        sys.path.insert(0, str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location("digest_check_" + name, ROOT / "scripts" / (name + ".py"))
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def _class_summary(unlisted):
    """The unlisted runs grouped by file kind, length and the text before the run; never the value."""
    groups = {}
    for item in unlisted:
        kind = re.sub(r"^[^/]*/", "", item["file"]).rsplit("/", 1)[-1]
        kind = "(uuid).%s" % kind.rsplit(".", 1)[-1] if re.match(r"[0-9a-f]{8}-", kind) else kind
        key = (kind, item["length"], re.sub(r"[0-9]", "9", item["before"][-24:]))
        groups[key] = groups.get(key, 0) + 1
    return "; ".join("%d x %s, %d hex, after %r" % (count, *key) for key, count in sorted(groups.items(), key=lambda row: -row[1])[:8])


@pytest.mark.parametrize("configuration", sorted(PUBLIC_SETS))
def test_every_current_public_set_holds_only_bound_digests(configuration):
    directory, inner, script, pattern = PUBLIC_SETS[configuration]
    base = PREPARATION / directory
    packets = sorted(path for path in (base / inner).iterdir() if (path / "manifest.json").is_file())
    assert len(packets) == 3
    receipts = [json.loads((base / pattern.format(run=packet.name)).read_bytes()) for packet in packets]
    import session_bench.public_digest_check as module_under_test
    assert not hasattr(module_under_test, "RECORDED_LEGACY_HEX_RUNS")
    module = _sanitizer(script)
    # ``report``: a failure then names the classes that still hold hits; the gate itself fails by default.
    result = check_public_packets(packets, receipts=receipts, extra_files=[base / "public-inputs-candidate.json"],
                                  allowlist=module.DIGEST_ALLOWLIST, hex_allowlist=getattr(module, "HEX_ALLOWLIST", ()), hex_runs="report")
    assert result["unbound"] == [] and result["classes"]["public_bytes"] > 0
    unlisted = result["hex_runs"]["unlisted"]
    assert not unlisted, "%d unlisted hex run(s) of 12 or more characters in the %s set (%d files): %s" % (
        len(unlisted), configuration, len({item["file"] for item in unlisted}), _class_summary(unlisted))


def test_long_hex_runs_are_classified_and_the_rest_is_unlisted():
    from session_bench.public_digest_check import UnlistedHexRunError, long_hex_runs
    line = b'{"type":"user","text":"hello"}'
    account = hashlib.sha1(b'{"account_id":"a","chatgpt_user_id":"u"}').hexdigest()
    contents = {
        "run-1/native/session.jsonl": line + b"\n",
        "run-1/inputs/ids.json": canonical({"zeroed": "0" * 32, "md5_of_a_public_line": hashlib.md5(line).hexdigest(),
                                            "truncated": sha(line)[:40], "sha256": sha(line), "hex_alphabet": "0123456789abcdef",
                                            "call": "call_" + "ab12" * 8, "frame": {"blob_hex": "28b52ffd" + "9f" * 20}}),
        "run-1/inputs/inventory.json": canonical({"entries": [{"relative_path": "cache/apps/" + account + ".json"}], "upper": "AB" * 32}),
    }
    result = long_hex_runs(contents, public_references={})
    assert result["classes"] == {"zero": 1, "decimal": 0, "constant": 1, "derivable": 2, "uuid": 0, "time_id": 0, "provider_id": 0, "fragment": 0, "allowlisted": 0}
    # No provider id is accepted without a reviewed field: the call id and the frame are unlisted, as are the account name and the upper-case run.
    assert [(item["file"], item["length"]) for item in result["unlisted"]] == [
        ("run-1/inputs/ids.json", 32), ("run-1/inputs/ids.json", 48), ("run-1/inputs/inventory.json", 40), ("run-1/inputs/inventory.json", 64)]
    # A file name that is a digest of private values is found, with the bytes before it and without its value.
    assert result["unlisted"][2]["before"].endswith("cache/apps/") and account not in json.dumps(result["unlisted"])
    assert UnlistedHexRunError.__mro__[1] is ValueError and result["legacy_rules_ignored"] == 0 and result["minimum"] == 12


def test_a_truncated_private_hash_is_unlisted_in_an_ordinary_field_a_file_name_and_a_compressed_member():
    from compression import zstd
    from session_bench.public_digest_check import long_hex_runs
    private = sha(b"/Users/real/work")
    line = b'{"type":"user","text":"hello"}'
    contents = {
        "run-1/native/session.jsonl": line + b"\n",
        "run-1/inputs/note.json": canonical({"summary": "see " + private[:20], "title": "x"}),   # the reviewer's 20-character case
        "run-1/inputs/cache/" + private[:16] + ".json": b"{}",                                    # a path name
        "run-1/native/member.zst": zstd.compress(b'{"text":"' + private[:24].encode() + b'"}'),   # only visible after decompression
        "run-1/inputs/ok.json": canonical({"summary": "see " + sha(line)[:20], "short": private[:11], "upper": sha(line)[:14].upper()}),
    }
    result = long_hex_runs(contents, public_references={})
    assert sorted({(item["file"], item["length"]) for item in result["unlisted"]}) == [  # a small frame may also hold the text as it is
        ("run-1/inputs/cache/" + private[:16] + ".json", 16), ("run-1/inputs/note.json", 20), ("run-1/native/member.zst", 24)]
    assert result["classes"]["derivable"] == 2  # a prefix of the SHA-256 of a public line, also in upper case; 11 characters are below the bound
    assert private[:20] not in json.dumps(result["unlisted"])
    # The bound is a parameter: 11 characters pass the default gate and are found with a bound of 8.
    assert len({(item["file"], item["length"]) for item in long_hex_runs(contents, public_references={}, minimum=8)["unlisted"]}) == 4


def test_check_public_packets_fails_by_default_on_a_short_hex_run_in_any_file(tmp_path):
    from session_bench.public_digest_check import UnlistedHexRunError
    packet = tmp_path / "run-1"
    (packet / "inputs").mkdir(parents=True)
    (packet / "manifest.json").write_bytes(canonical({"configuration_id": "new-row"}))
    (packet / "inputs/note.json").write_bytes(canonical({"summary": "built from " + sha(b"/Users/real/work")[:20]}))
    with pytest.raises(UnlistedHexRunError, match="note.json"):
        check_public_packets([packet])
    assert len(check_public_packets([packet], hex_runs="report")["hex_runs"]["unlisted"]) == 1
    with pytest.raises(ValueError, match="fail or report"):
        check_public_packets([packet], hex_runs="skip")
    # A prefix of 11 characters is gated only as the value of a digest-like key (review of v39, earlier finding 11).
    # Under an ordinary key it stays below the bound of 12: a limit of the gate, written in the module.
    (packet / "inputs/note.json").write_bytes(canonical({"summary": "built from " + sha(b"/Users/real/work")[:11]}))
    assert check_public_packets([packet])["hex_runs"]["unlisted"] == []
    (packet / "inputs/note.json").write_bytes(canonical({"summary": "built", "hash": sha(b"/Users/real/work")[:11]}))
    with pytest.raises(UnlistedHexRunError, match="note.json"):
        check_public_packets([packet])


def test_a_short_hex_value_under_a_digest_like_key_must_be_zeros_derivable_or_proved():
    from session_bench.public_digest_check import long_hex_runs
    line = b'{"type":"user","text":"hello"}'
    private = sha(b"/Users/real/work")
    keys = ["hash", "content_digest", "sha", "checksum", "fingerprint", "etag", "signature", "Hmac", "thumbprint"]
    contents = {"run-1/native/session.jsonl": line + b"\n",
                "run-1/inputs/ok.json": canonical({"hash": sha(line)[:11], "digest": sha(line)[:6].upper(), "crc": "00000000", "md5": hashlib.md5(line).hexdigest()[:9]}),
                "run-1/inputs/bad.json": canonical({key: private[:11] for key in keys}),
                "run-1/inputs/lengths.json": canonical({"hash": private[:6], "digest": private[:8], "sha": private[:10]}),
                "run-1/inputs/text.json": canonical({"event": json.dumps({"inner": {"file_hash": private[:7]}})}),
                "run-1/inputs/plain.json": canonical({"summary": private[:11], "title": private[:7]})}   # not a digest-like key: not gated
    result = long_hex_runs(contents, public_references={})
    assert sorted({item["file"] for item in result["unlisted"]}) == ["run-1/inputs/bad.json", "run-1/inputs/lengths.json", "run-1/inputs/text.json"]
    assert len(result["unlisted"]) == len(keys) + 3 + 1
    assert result["classes"]["zero"] == 1 and result["classes"]["derivable"] == 3
    # A value that a rule proves is accepted; a pin that does not hold gives no allowance.
    rule = {"file": "bad.json", "before": rb'"hash":"', "proof": lambda run: run.token.decode() == private[:11], "reason": "a value that the rule writes down in full"}
    result = long_hex_runs({k: v for k, v in contents.items() if k.endswith(("session.jsonl", "bad.json"))}, hex_allowlist=[rule], public_references={})
    assert result["classes"]["allowlisted"] == 1 and len(result["unlisted"]) == len(keys) - 1


def test_a_digest_in_the_name_of_a_file_or_a_directory_is_gated_like_a_digest_in_a_file(tmp_path):
    from session_bench.public_digest_check import UnboundDigestError, UnlistedHexRunError, long_hex_runs
    line = b'{"type":"user","text":"hello"}'
    private = sha(b"/Users/real/work")
    packet = tmp_path / "run-1"
    (packet / "inputs").mkdir(parents=True)
    (packet / "manifest.json").write_bytes(canonical({"configuration_id": "new-row"}))
    (packet / "inputs/session.jsonl").write_bytes(line + b"\n")
    # A public digest as a name is fine, in a file and in an empty directory.
    (packet / "inputs" / (sha(line) + ".json")).write_bytes(b"{}")
    (packet / "inputs" / ("cache-" + sha(line))).mkdir()
    assert check_public_packets([packet])["unbound"] == []
    # The reviewer's case: the exact 64-hex name of a file escapes both gates. Now the digest check fails ...
    (packet / "inputs" / (private + ".json")).write_bytes(b"{}")
    with pytest.raises(UnboundDigestError, match="inputs"):
        check_public_packets([packet])
    (packet / "inputs" / (private + ".json")).unlink()
    # ... also for the name of an empty directory (a file name holds it twice: here once, in a directory).
    (packet / "inputs" / ("x" + private)).mkdir()
    with pytest.raises(UnboundDigestError):
        check_public_packets([packet])
    (packet / "inputs" / ("x" + private)).rmdir()
    # A hex run of 12 to 63 characters or an upper-case 64 in a directory name is found by the hex gate.
    (packet / "inputs" / ("d-" + private[:30])).mkdir()
    with pytest.raises(UnlistedHexRunError):
        check_public_packets([packet])
    (packet / "inputs" / ("d-" + private[:30])).rmdir()
    (packet / "inputs" / ("D-" + private.upper())).mkdir()
    with pytest.raises(UnlistedHexRunError):
        check_public_packets([packet])
    (packet / "inputs" / ("D-" + private.upper())).rmdir()
    assert check_public_packets([packet])["unbound"] == []
    # long_hex_runs sees a 64-hex name too (a name of a file in the contents).
    contents = {"run-1/native/session.jsonl": line + b"\n", "run-1/native/" + private + ".bin": b"x", "run-1/native/" + sha(line) + ".bin": b"x"}
    unlisted = long_hex_runs(contents, public_references={})["unlisted"]
    assert [(item["file"], item["length"]) for item in unlisted] == [("run-1/native/" + private + ".bin", 64)]


def test_numbers_uuids_times_and_fragments_are_shapes_and_other_values_are_not():
    import uuid
    from session_bench.public_digest_check import long_hex_runs
    clock = 1790733533802
    identifier = lambda counter: ((clock % (1 << 36)) << 12 | counter).to_bytes(6, "big").hex()
    contents = {
        "run-1/native/times.json": canonical({"ms": clock, "ns": 1791402802028000000, "ratio": 0.007645000000000001, "migration": "20260622202450_x",
                                              "quoted": "123456789012345"}),
        "run-1/native/ids.json": canonical({"random": str(uuid.UUID(bytes=b"\x11" * 6 + b"\x40\x11" + b"\x80" + b"\x22" * 7)),
                                            "seven": "019a0000-0000-7000-8000-123456789abc",
                                            "name_based": str(uuid.uuid5(uuid.NAMESPACE_DNS, "private.example"))}),
        "run-1/native/ascending.bin": b"\x00\x01ses_" + identifier(1).encode() + b"AbCdEfGhIjKlMn\x00" + b"\x01evt_" + identifier(3).encode() + b"zz\x00"
                                      + b"\x01ses_" + sha(b"private")[:12].encode() + b"AbCdEfGhIjKlMn\x00" + b"\x01" + (identifier(1) + "AbCdEf")[1:13].encode() + b"\x00",
    }
    result = long_hex_runs(contents, public_references={})
    assert result["classes"]["decimal"] == 4 and result["classes"]["uuid"] == 2 and result["classes"]["time_id"] == 2 and result["classes"]["fragment"] == 1
    flagged = sorted((item["file"], item["length"]) for item in result["unlisted"])
    # A quoted digit string that is no time, a name-based UUID (a SHA-1) and an id whose time is far from every public time.
    assert flagged == [("run-1/native/ascending.bin", 18), ("run-1/native/ids.json", 12), ("run-1/native/times.json", 15)]


def _provider_set(value, field="id", extra=b""):
    return {"run-1/native/rows.json": canonical({"rows": [{field: value}]}) + extra}


def test_a_provider_id_counts_only_as_the_whole_value_of_a_reviewed_field_with_a_known_hex_length():
    from session_bench.public_digest_check import long_hex_runs
    random_call = "call_" + "ab12" * 8
    run = lambda contents, fields=("id",): long_hex_runs(contents, public_references={}, provider_id_fields=fields)
    assert run(_provider_set(random_call))["classes"]["provider_id"] == 1
    assert run(_provider_set("message:msg_" + "cd34" * 12 + "00", "event_id"), ("event_id",))["classes"]["provider_id"] == 1  # a label may stand before the id
    # The reviewer's failure: call_<SHA-1 of a private value> (40 hex characters) is not a provider id shape.
    private = hashlib.sha1(b"/Users/real/work").hexdigest()
    assert len(run(_provider_set("call_" + private))["unlisted"]) == 1
    assert len(run(_provider_set("msg_" + private))["unlisted"]) == 1
    # Free text, a field that is not reviewed, a configuration without reviewed fields, and an id with text after it are unlisted.
    assert len(run(_provider_set("see " + random_call + " for the call", "text"))["unlisted"]) == 1
    assert len(run(_provider_set(random_call, "note"))["unlisted"]) == 1
    assert len(run(_provider_set(random_call), ())["unlisted"]) == 1
    assert len(run(_provider_set(random_call + "xyz_not_an_id"))["unlisted"]) == 1
    # An object key and a list item count against the parsed JSON; an id in a document that stops early counts by its bytes.
    keyed = {"run-1/native/rows.json": canonical({"tool_call_uids": json.dumps({random_call: "x"}), "ids": ["a:" + random_call]})}
    assert run(keyed, ("key:tool_call_uids", "ids"))["classes"]["provider_id"] == 2
    assert len(run(keyed, ("ids",))["unlisted"]) == 1
    assert run({"run-1/native/page.bin": b'\x01\x02{"type":"tool","callID":"%s","state":{"status":"pen' % random_call.encode()}, ("callID",))["classes"]["provider_id"] == 1
    # A second use of a keyed id that the parsed JSON does not hold is surplus.
    assert len(run({"run-1/native/rows.json": keyed["run-1/native/rows.json"] + b"\n" + random_call.encode()}, ("key:tool_call_uids", "ids"))["unlisted"]) == 1


def test_provider_id_fields_are_written_down_per_configuration_and_every_current_id_is_in_one():
    from session_bench.public_digest_check import PROVIDER_ID_FIELDS, PROVIDER_ID_SHAPES
    assert sorted(PROVIDER_ID_FIELDS) == ["codex-cli", "hermes", "openclaw", "opencode-cli", "pi"]
    assert all(fields and len(set(fields)) == len(fields) for fields in PROVIDER_ID_FIELDS.values())
    assert PROVIDER_ID_SHAPES["call"] == (32,) and set(PROVIDER_ID_SHAPES) == {"msg", "rs", "fc", "call", "resp", "ctc"}


def test_hex_rules_need_a_proof_and_a_reason_and_a_rule_without_a_proof_gives_no_allowance():
    from session_bench.public_digest_check import _hex_rules, long_hex_runs
    legacy = {"file": "ids.json", "before": rb'"blob_hex":"', "reason": "bytes of a compressed public frame, not a digest"}
    contents = {"run-1/inputs/ids.json": canonical({"frame": {"blob_hex": "28b52ffd" + "9f" * 20}})}
    result = long_hex_runs(contents, hex_allowlist=[legacy], public_references={})
    assert result["classes"]["allowlisted"] == 0 and len(result["unlisted"]) == 1 and result["legacy_rules_ignored"] == 1
    proved = {**legacy, "proof": lambda run: run.token.startswith(b"28b52ffd")}
    result = long_hex_runs(contents, hex_allowlist=[proved], public_references={})
    assert result["classes"]["allowlisted"] == 1 and result["unlisted"] == [] and list(result["allowlisted_by_reason"].values()) == [1]
    assert long_hex_runs({"run-1/inputs/ids.json": canonical({"frame": {"blob_hex": "99" * 20}})}, hex_allowlist=[proved], public_references={})["classes"]["allowlisted"] == 0
    with pytest.raises(ValueError, match="written reason"):
        _hex_rules([{**proved, "reason": "ok"}])
    with pytest.raises(ValueError, match="function"):
        _hex_rules([{**proved, "proof": "yes"}])
    with pytest.raises(ValueError, match="pin"):
        _hex_rules([{**proved, "pin": {"sha256": "00"}}])


def test_a_pin_fixes_the_count_and_the_values_of_a_rule_and_a_changed_set_gets_no_allowance():
    from session_bench.public_digest_check import long_hex_runs
    values = ["9f" * 20, "8e" * 20]
    contents = {"run-1/inputs/ids.json": canonical({"frames": [{"blob_hex": value} for value in values]})}
    pinned = {"file": "ids.json", "before": rb'"blob_hex":"', "proof": lambda run: True, "reason": "bytes of a compressed public frame, not a digest",
              "pin": {"count": 2, "sha256": hashlib.sha256("\n".join(sorted(values)).encode()).hexdigest()}}
    assert long_hex_runs(contents, hex_allowlist=[pinned], public_references={})["classes"]["allowlisted"] == 2
    contents["run-1/inputs/ids.json"] = canonical({"frames": [{"blob_hex": value} for value in [values[0], "7d" * 20]]})
    result = long_hex_runs(contents, hex_allowlist=[pinned], public_references={})
    assert result["classes"]["allowlisted"] == 0 and len(result["unlisted"]) == 2 and result["unlisted"][0]["before"] == "(rule pin failed)"
    contents["run-1/inputs/ids.json"] = canonical({"frames": [{"blob_hex": value} for value in [*values, "7d" * 20]]})
    assert long_hex_runs(contents, hex_allowlist=[pinned], public_references={})["classes"]["allowlisted"] == 0


def _database(rows, columns="k TEXT, v"):
    """The bytes of a real SQLite file with one table."""
    import sqlite3
    import tempfile
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "x.db"
        connection = sqlite3.connect(path)
        connection.execute("PRAGMA journal_mode=OFF")
        connection.execute("CREATE TABLE t(%s)" % columns)
        connection.executemany("INSERT INTO t VALUES (?, ?)", rows)
        connection.commit(); connection.close()
        return path.read_bytes()


def test_a_hex_run_is_derivable_only_as_a_whole_prefix_of_one_complete_digest():
    """Review of v39, P1: 24 public characters plus 16 arbitrary ones, or a public digest plus other hex, are not derivable."""
    from session_bench.public_digest_check import long_hex_runs
    line = b'{"type":"user","text":"hello"}'
    public, private = sha(line), sha(b"operator@example.com")
    email = b"operator@example.com".hex()
    contents = {"run-1/native/session.jsonl": line + b"\n",
                "run-1/inputs/whole.json": canonical({"a": public[:40], "b": public[:24], "c": public, "d": hashlib.sha1(line).hexdigest()}),
                "run-1/inputs/mixed.json": canonical({"first24": public[:24] + private[:16],                # the reviewer's 40-character case
                                                      "past_end": hashlib.sha1(line).hexdigest() + "abcdef0123456789",   # longer than a SHA-1
                                                      "digest_then_email": public + email,                 # longer than 64
                                                      "email_then_digest": email + public,
                                                      "digest_then_private": public + private[:20]})}
    result = long_hex_runs(contents, public_references={})
    assert result["classes"]["derivable"] == 3  # the 40 and 24 character prefixes and the SHA-1; the 64-hex digest is not counted here
    assert sorted(item["length"] for item in result["unlisted"]) == [40, 56, 84, 104, 104]
    assert all(item["file"] == "run-1/inputs/mixed.json" for item in result["unlisted"])
    assert email not in json.dumps(result["unlisted"])
    # A split into parts that each pass on their own is derivable: a public digest with zeros or with another public digest.
    other = hashlib.sha256(b"x" * 40).hexdigest()
    contents = {"run-1/native/session.jsonl": line + b"\n", "run-1/native/other.txt": b"x" * 40,
                "run-1/inputs/parts.json": canonical({"zeros": public + "0" * 20, "two": public + other[:20], "short_tail": public + "ab"})}
    result = long_hex_runs(contents, public_references={})
    assert [item["length"] for item in result["unlisted"]] == [66] and result["classes"]["derivable"] == 2


def test_a_hex_run_longer_than_64_in_a_sqlite_file_needs_a_text_cell_that_is_the_public_digest_and_framing_bytes_only():
    from session_bench.public_digest_check import long_hex_runs
    line = b'{"type":"user","text":"hello"}'
    blob = sha(line)
    session = {"run-1/native/session.jsonl": line + b"\n"}
    # A row (digest, 97): the integer 97 is the byte "a" next to the digest, in the file and framing of the record.
    framed = _database([(blob, 97), (blob, 98)])
    assert re.search(rb"[0-9a-f]{65,}", framed)
    result = long_hex_runs({**session, "run-1/native/store.db": framed}, public_references={})
    assert result["classes"]["derivable"] >= 1 and result["unlisted"] == []
    # The same bytes outside a SQLite file: unlisted.
    bare = b"\x04\x81\r\x01" + blob.encode() + b"a" + b"\x00"
    assert len(long_hex_runs({**session, "run-1/native/store.bin": bare}, public_references={})["unlisted"]) == 1
    # A digest of private bytes in the database; a public digest next to a private text cell of the same record.
    private = hashlib.sha256(b"private").hexdigest()
    assert len(long_hex_runs({**session, "run-1/native/store.db": _database([(private, 97)])}, public_references={})["unlisted"]) == 1
    tail = _database([(blob, "ab12cd34ef56ab78")])
    unlisted = long_hex_runs({**session, "run-1/native/store.db": tail}, public_references={})["unlisted"]
    assert len(unlisted) == 1 and "ab12cd34ef56ab78" not in json.dumps(unlisted)


def test_check_public_packets_reads_an_iterator_of_packets_once_and_still_checks_the_names(tmp_path):
    """Review of v40, P2: the first pass over an iterator used all of it, and the name check saw no packet."""
    packet = tmp_path / "run-1"
    (packet / "native").mkdir(parents=True)
    (packet / "native/session.jsonl").write_bytes(b'{"type":"user"}\n')
    (packet / "manifest.json").write_bytes(b"{}")
    clean = check_public_packets(iter([packet]), hex_runs="report")
    assert clean["unbound"] == [] and clean["hex_runs"]["unlisted"] == []
    (packet / "native" / hashlib.sha256(b"operator@example.com").hexdigest()).mkdir()  # an empty directory named by a private digest
    for packets in ([packet], iter([packet]), (path for path in [packet]), [str(packet)], iter([str(packet)])):
        with pytest.raises(UnboundDigestError):
            check_public_packets(packets)
    result = classify_public_digests(read_public_set(iter([packet])), extra_names=public_entry_names([packet]))
    assert [item["where"] for item in result["unbound"]] == ["name"]


def test_sqlite_framing_needs_a_parsed_cell_not_a_record_header_inside_a_blob():
    """Review of v40, P1: bytes 04 81 0d 06, a public digest as hex text and 8 private hex characters, inside a BLOB."""
    import sqlite3
    import tempfile
    from session_bench.public_digest_check import long_hex_runs
    line = b'{"type":"user","text":"hello"}'
    blob = sha(line)
    session = {"run-1/native/session.jsonl": line + b"\n"}
    private = "c0ffee12"
    fabricated = b"\x04\x81\r\x06" + blob.encode() + private.encode() + b"\x00"
    stores = {"in a BLOB": _database([("a", fabricated)]),
              "in a BLOB, last field": _database([("a", b"x"), ("b", fabricated)]),
              "in a BLOB, framed by real integers": _database([(97, fabricated)], columns="k INTEGER, v BLOB")}
    for label, store in stores.items():
        assert re.search(blob.encode() + private.encode(), store), label
        unlisted = long_hex_runs({**session, "run-1/native/store.db": store}, public_references={})["unlisted"]
        assert len(unlisted) == 1 and private not in json.dumps(unlisted), label
    # A real record is still accepted: the digest is a whole TEXT field and an integer follows. The same bytes as a BLOB field are not.
    real = _database([(blob, 97)])
    assert long_hex_runs({**session, "run-1/native/store.db": real}, public_references={})["unlisted"] == []
    assert len(long_hex_runs({**session, "run-1/native/store.db": _database([(blob.encode(), 97)])}, public_references={})["unlisted"]) == 1
    # A deleted row stays in a freeblock or in free space: no parsed cell holds it.
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "x.db"
        connection = sqlite3.connect(path)
        connection.execute("PRAGMA journal_mode=OFF"); connection.execute("PRAGMA secure_delete=OFF")
        connection.execute("CREATE TABLE t(k TEXT, v)")
        connection.executemany("INSERT INTO t VALUES (?, ?)", [(blob, 97), ("x" * 10, 1), ("y" * 10, 2)])
        connection.execute("DELETE FROM t WHERE k = ?", (blob,))
        connection.commit(); connection.close()
        freed = path.read_bytes()
    assert re.search(rb"[0-9a-f]{65,}", freed)
    assert len(long_hex_runs({**session, "run-1/native/store.db": freed}, public_references={})["unlisted"]) == 1
    # A cell with an overflow page: the digest in the local part passes; hex in the local BLOB bytes next to it does not.
    overflow = _database([(blob, b"z" * 9003)])  # the serial type of the BLOB ends in the byte 0x62, so the run is 65 characters
    assert re.search(rb"[0-9a-f]{65}", overflow)
    assert long_hex_runs({**session, "run-1/native/store.db": overflow}, public_references={})["unlisted"] == []
    suffixed = _database([(blob, private.encode() + b"z" * 8995)])
    unlisted = long_hex_runs({**session, "run-1/native/store.db": suffixed}, public_references={})["unlisted"]
    assert len(unlisted) == 1 and unlisted[0]["length"] == 73 and private not in json.dumps(unlisted)
    # A digest that lies in the overflow part, or crosses into it, is no field of a parsed cell.
    from session_bench.public_digest_check import _SqliteCells
    for lead in [b"z" * 9000, *(b"z" * size for size in range(4040, 4140, 7))]:  # 4040 to 4140: the local part ends in or near the digest
        data = _database([(lead, blob)], columns="k BLOB, v TEXT")
        cells = _SqliteCells(data)
        start = data.find(blob.encode())
        assert start < 0 or not any((141, start, start + 64) in cell[3] for cell in cells.cells), len(lead)
    unreached = bytearray(real)
    unreached[103:105] = b"\x00\x00"
    assert len(long_hex_runs({**session, "run-1/native/store.db": bytes(unreached)}, public_references={})["unlisted"]) == 1


def test_the_sqlite_cell_parser_lists_the_cells_of_reachable_pages_only():
    from session_bench.public_digest_check import _SqliteCells
    rows = [("k%03d" % number, number) for number in range(400)]
    cells = _SqliteCells(_database(rows))
    # 400 table cells of two fields; a table of this size has interior pages whose cells hold no record.
    assert sum(1 for cell in cells.cells if len(cell[3]) == 2) == 400
    assert all(cell[2] > cell[0] for cell in cells.cells)
    assert not _SqliteCells(b"not a database").cells
    assert not _SqliteCells(b"SQLite format 3\x00" + b"\xff" * 200).cells


def test_a_repeated_json_key_keeps_every_member():
    """Review of v40, P2: the first value of a repeated key is hidden by ``dict``."""
    from session_bench.public_digest_check import _digest_keyed_short, _documents, _strings, long_hex_runs, short_digest_warnings
    document = b'{"hash":"a1b2c3d4","hash":"00000000"}'
    assert _digest_keyed_short(_documents(document)) == {"a1b2c3d4", "00000000"}
    assert [text for _, text in _strings(_documents(document)[0]) if text != "hash"] == ["a1b2c3d4", "00000000"]
    # Whole document, a line of a log, and a document embedded in a string and in a longer text.
    for label, data in {"whole": document, "line": b'{"a":1}\n' + document + b"\n", "embedded": b"prefix " + document + b" suffix",
                        "in a string": json.dumps({"x": document.decode()}).encode(), "nested": b'{"o":{"sha":"a1b2c3d4","sha":"00000000"}}'}.items():
        unlisted = long_hex_runs({"run-1/inputs/file.json": data}, public_references={})["unlisted"]
        assert [item["length"] for item in unlisted] == [8], label
        if label != "embedded":  # the warning list reads whole documents, lines and JSON text in strings
            assert [item["value"] for item in short_digest_warnings({"run-1/inputs/file.json": data}) if item["value"] == "a1b2c3d4"], label
    assert not long_hex_runs({"run-1/inputs/file.json": b'{"hash":"00000000","hash":"00000000"}'}, public_references={})["unlisted"]


def test_a_directory_named_by_the_md5_of_a_public_path_string_is_derivable():
    from session_bench.public_digest_check import long_hex_runs
    path = "/Users/xxxx/work/space"
    contents = {"run-1/inputs/plan.json": canonical({"args": ["--workspace", path]}),
                "run-1/inputs/root.json": canonical({"entries": ["chats/" + hashlib.md5(path.encode()).hexdigest() + "/store.db",
                                                                 "chats/" + hashlib.md5(b"/Users/real/work").hexdigest() + "/store.db"]})}
    result = long_hex_runs(contents, public_references={})
    assert result["classes"]["derivable"] == 1 and len(result["unlisted"]) == 1


def test_the_configuration_rules_name_a_reason_a_proof_and_are_checked_by_the_rule_validator():
    from session_bench.public_digest_check import CONFIGURATION_HEX_ALLOWLIST, _hex_rules
    assert sorted(CONFIGURATION_HEX_ALLOWLIST) == ["antigravity", "codex-cli", "copilot", "cursor-cli", "hermes", "openclaw"]
    for rules in CONFIGURATION_HEX_ALLOWLIST.values():
        parsed, ignored = _hex_rules(rules)
        assert parsed and ignored == 0 and all(callable(rule["proof"]) for rule in parsed)


def _rule(configuration, index=0, pin=True):
    from session_bench.public_digest_check import CONFIGURATION_HEX_ALLOWLIST
    rule = dict(CONFIGURATION_HEX_ALLOWLIST[configuration][index])
    if not pin:
        rule.pop("pin", None)
    return rule


def test_copilot_encoded_path_data_must_decode_to_the_public_path_beside_it():
    from session_bench.public_digest_check import long_hex_runs
    path = "/private/var/folders/xx/" + "x" * 30 + "/T/session-bench-copilot-abcdefgh/fixture_project/checkout.py"
    index = lambda data: json.dumps({"snapshots": [{"files": {"k": {"path": path, "preimage": {"resolvedPath": path, "encodedPath": {
        "encoding": "unix-bytes", "data": data}}}}}]}, indent=2).encode()
    contents = lambda data: {"run-1/native/rewind-file-snapshots/index.json": index(data)}
    rules = [_rule("copilot", 0)]
    assert long_hex_runs(contents(path.encode().hex()), hex_allowlist=rules, public_references={})["classes"]["allowlisted"] == 1
    # The reviewer's failure: any private-derived digest in the same position.
    arbitrary = hashlib.sha1(b"/Users/real/work").hexdigest()
    result = long_hex_runs(contents(arbitrary), hex_allowlist=rules, public_references={})
    assert result["classes"]["allowlisted"] == 0 and [item["length"] for item in result["unlisted"]] == [40]
    other = (path.replace("checkout", "private")).encode().hex()  # a path that is not in the file
    assert long_hex_runs(contents(other), hex_allowlist=rules, public_references={})["classes"]["allowlisted"] == 0


def test_copilot_vendor_hashes_must_be_equal_in_every_packet_and_only_the_pinned_values_pass():
    from session_bench.public_digest_check import long_hex_runs
    def packet(number, environment):
        record = {"type": "request", "data": {"tools": [{"name": "bash", "schema_hash": "5aff88e14e77"}],
                                              "system_segments": [{"segment": "tone_and_style", "hash": "866a6130c416", "tokens": 5},
                                                                  {"segment": "environment_context", "hash": environment, "tokens": 9}]}}
        return {"run-%d/manifest.json" % number: canonical({"configuration_id": "copilot"}), "run-%d/native/events.jsonl" % number: canonical(record) + b"\n"}
    contents = {**packet(1, "70e94391c5de"), **packet(2, "9a4d328a5cec"), **packet(3, "0b1c2d3e4f50")}
    result = long_hex_runs(contents, hex_allowlist=[_rule("copilot", 1, pin=False)], public_references={})
    assert result["classes"]["allowlisted"] == 6  # the tool hash and the style hash of three packets
    assert len(result["unlisted"]) == 3 and {item["file"][:5] for item in result["unlisted"]} == {"run-1", "run-2", "run-3"}
    # The pin of the rule fixes the 42 values of the ranked set; any other set gets no allowance.
    assert long_hex_runs(contents, hex_allowlist=[_rule("copilot", 1)], public_references={})["classes"]["allowlisted"] == 0


def _store(meta, name="8b045f58-e256-4ffc-bd5e-39b844adb5c9"):
    return {"run-1/native/chats/" + name + "/store.db": b"\x00\x00\x07\x04\x0f\x01]" + json.dumps(meta).encode().hex().encode() + b"\x00",
            "run-1/native/public.json": canonical({"blob": "x"})}


def test_cursor_meta_row_must_decode_to_json_without_a_private_value():
    from session_bench.public_digest_check import long_hex_runs
    root = {"run-1/native/blob.bin": b"public blob bytes"}
    meta = {"agentId": "8b045f58-e256-4ffc-bd5e-39b844adb5c9", "latestRootBlobId": sha(b"public blob bytes"), "name": "New Agent", "createdAt": 1791339618300,
            "mode": "default", "isRunEverything": True, "blobEncryptionKey": "0" * 64}
    rules = [_rule("cursor-cli")]
    check = lambda value, name="8b045f58-e256-4ffc-bd5e-39b844adb5c9": long_hex_runs({**_store(value, name), **root}, hex_allowlist=rules, public_references={})
    assert check(meta)["classes"]["allowlisted"] == 1
    assert len(check({**meta, "owner": "operatorname"})["unlisted"]) == 1                      # a string outside the vocabulary
    assert len(check({**meta, "latestRootBlobId": sha(b"private bytes")})["unlisted"]) == 1   # a digest that public bytes do not yield
    assert len(check({**meta, "blobEncryptionKey": sha(b"key")})["unlisted"]) == 1
    assert len(check(meta, "c1ea7e3b-6516-480a-a178-fda64f1dcd57")["unlisted"]) == 1         # an agent id that names no directory of the set
    # Review of v39, P1: the keys are validated as well as the values, against the reviewed schema of the meta row.
    from session_bench.public_digest_check import CURSOR_META_ROW_SCHEMA
    assert sorted(CURSOR_META_ROW_SCHEMA) == ["agentId", "blobEncryptionKey", "createdAt", "isRunEverything", "latestRootBlobId", "mode", "name"]
    assert len(check({"operator@example.com": 0})["unlisted"]) == 1                            # the reviewer's case
    assert len(check({**meta, "operator@example.com": 0})["unlisted"]) == 1                    # a private key beside the reviewed keys
    assert len(check({**meta, "name": {"operator@example.com": "New Agent"}})["unlisted"]) == 1  # a nested object
    assert len(check({**meta, "createdAt": 10 ** 30})["unlisted"]) == 1                        # a number that can carry data
    assert len(check({**meta, "mode": True})["unlisted"]) == 1                                 # a value of the wrong kind
    text = json.dumps(meta)
    repeated = '{"name": "operator@example.com", ' + text[1:]                                  # the second "name" would hide the first
    store = {**_store(meta), "run-1/native/chats/8b045f58-e256-4ffc-bd5e-39b844adb5c9/store.db": b"\x00\x00\x07\x04\x0f\x01]" + repeated.encode().hex().encode() + b"\x00"}
    assert len(long_hex_runs({**store, **root}, hex_allowlist=rules, public_references={})["unlisted"]) == 1


def _proto(*items):
    def varint(number):
        out = b""
        while True:
            byte = number & 0x7F; number >>= 7
            out += bytes([byte | (0x80 if number else 0)])
            if not number:
                return out
    return b"".join(varint(field << 3 | 2) + varint(len(value)) + value for field, value in items)


def test_antigravity_summary_hex_must_be_protobuf_with_only_public_or_uuid_strings():
    from session_bench.public_digest_check import long_hex_runs
    public = {"run-1/native/session.jsonl": canonical({"title": "Fix Checkout Total Calculation"}) + b"\n"}
    rules = [_rule("antigravity", 0)]
    file = "run-1/inputs/capture/shared-store-extract.json"
    check = lambda *items: long_hex_runs({**public, file: canonical({"row": {"raw_summary": {"hex": _proto(*items).hex()}}})}, hex_allowlist=rules, public_references={})
    uuid = b"d68e041c-5e75-4e5e-9493-ea1a657bd5ec"
    assert check((1, b"Fix Checkout Total Calculation"), (4, uuid), (5, _proto((1, uuid))))["classes"]["allowlisted"] == 1
    result = check((1, b"Fix Checkout Total Calculation"), (4, b"operatorname-private-workspace"))
    assert result["classes"]["allowlisted"] == 0 and len(result["unlisted"]) == 1
    assert len(check((1, bytes.fromhex(sha(b"private")[:32])))["unlisted"]) == 1        # a binary leaf that could be a digest
    assert len(long_hex_runs({**public, file: canonical({"row": {"raw_summary": {"hex": sha(b"private")[:40]}}})}, hex_allowlist=rules, public_references={})["unlisted"]) == 1


def test_codex_commit_hash_must_equal_the_pinned_commit_and_the_count():
    from session_bench.public_digest_check import long_hex_runs
    commit = "ba333c530ea2a561b5eea3a3f8772c209405eeff"
    one = lambda value: {"run-%d/native/rollout.jsonl" % number: b'{"git":{"commit_hash":"' + value.encode() + b'"}}\n' for number in (1, 2, 3)}
    assert long_hex_runs(one(commit), hex_allowlist=[_rule("codex-cli")], public_references={})["classes"]["allowlisted"] == 3
    result = long_hex_runs(one(hashlib.sha1(b"/Users/real/work").hexdigest()), hex_allowlist=[_rule("codex-cli", pin=False)], public_references={})
    assert result["classes"]["allowlisted"] == 0 and len(result["unlisted"]) == 3
    assert long_hex_runs({"run-1/native/rollout.jsonl": one(commit)["run-1/native/rollout.jsonl"]}, hex_allowlist=[_rule("codex-cli")], public_references={})["classes"]["allowlisted"] == 0


def test_hermes_uid_must_be_a_uuid4_in_the_field_of_its_own_row():
    from session_bench.public_digest_check import long_hex_runs
    uid = "9d2b4e8817a64b84a5e29e3a9d199f60"
    rows = lambda row: {"run-1/native/capture/session-store-rows.json": json.dumps({"rows": [row]}, indent=1).encode()}
    check = lambda row: long_hex_runs(rows(row), hex_allowlist=[_rule("hermes", pin=False)], public_references={})
    assert check({"id": 7, "message_uid": uid})["classes"]["allowlisted"] == 1
    assert check({"id": 7, "tool_call_uid": uid})["classes"]["allowlisted"] == 1
    assert len(check({"id": "7", "message_uid": uid})["unlisted"]) == 1                  # a row without its own integer id
    assert len(check({"id": 7, "message_uid": hashlib.md5(b"/Users/real/work").hexdigest()})["unlisted"]) == 1  # not UUID4 bits
    assert len(check({"id": 7, "note": "x", "message_uid": uid[:-1] + "1", "other": {"message_uid": uid}})["unlisted"]) == 1
    assert len(long_hex_runs(rows({"id": 7, "message_uid": uid}), hex_allowlist=[_rule("hermes")], public_references={})["unlisted"]) == 1  # the count pin


def test_openclaw_frame_must_decompress_to_json_that_adds_nothing_to_the_public_bytes():
    from compression import zstd
    from session_bench.public_digest_check import long_hex_runs
    public = {"run-1/native/prompt.json": canonical({"prompt": "This is a synthetic task", "type": "message", "id": "i", "timestamp": "t", "text": "x"})}
    frame = lambda document: {**public, "run-1/native/capture/session-store-rows.json": b'{\n "blob_hex": "' + zstd.compress(json.dumps(document).encode()).hex().encode() + b'"\n}'}
    rules = [_rule("openclaw", pin=False)]
    event = {"type": "message", "id": "26d7c867-c6f8-4380-8b57-b2434dfc3867", "timestamp": "2026-10-07T06:00:00.000Z", "message": {"text": "This is a synthetic task"}}
    assert long_hex_runs(frame(event), hex_allowlist=rules, public_references={})["classes"]["allowlisted"] == 1
    leaking = {**event, "message": {"text": "the home name is operatorname"}}
    assert len(long_hex_runs(frame(leaking), hex_allowlist=rules, public_references={})["unlisted"]) == 1
    assert len(long_hex_runs({**public, "run-1/native/capture/session-store-rows.json": b'{\n "blob_hex": "' + sha(b"p").encode()[:40] + b'"\n}'}, hex_allowlist=rules, public_references={})["unlisted"]) == 1


def test_the_gate_module_is_embedded_in_no_ranked_packet_runtime():
    for directory, inner, *_ in PUBLIC_SETS.values():
        base = PREPARATION / directory
        runtimes = list((base / inner).glob("*/runtime"))
        assert len(runtimes) == 3
        assert not [path for runtime in runtimes for path in runtime.rglob("*") if "public_digest_check" in path.name]


def _rewind_indexes(base):
    return sorted(base.rglob("rewind-file-snapshots/index.json"))


def test_copilot_rewind_file_keys_are_hashes_of_the_public_path_and_the_private_derived_keys_are_gone():
    base = PREPARATION / "copilot-public-candidates-v10"
    indexes = _rewind_indexes(base)
    assert len(indexes) == 6  # a native copy and a capture copy in each of three packets
    public = set()
    for path in indexes:
        for snapshot in json.loads(path.read_bytes())["snapshots"]:
            for key, record in snapshot["files"].items():
                assert key == sha(record["path"].encode())[:32] and "/var/folders/xx/" in record["path"]
                public.add(key)
    assert len(public) == 3
    old = PREPARATION / "copilot-public-candidates-v7"
    if old.is_dir():  # the keys of the rejected set were derived from private paths
        keys = {key for path in _rewind_indexes(old) for snapshot in json.loads(path.read_bytes())["snapshots"] for key in snapshot["files"]}
        assert len(keys) == 3 and not keys & public
        for path in sorted(base.rglob("*")):
            if path.is_file() and not path.is_symlink():
                data = path.read_bytes()
                assert not any(key.encode() in data for key in keys), path.name


def test_the_copilot_sanitizer_rekeys_rewind_keys_to_the_public_path_at_equal_length():
    module = _sanitizer("sanitize_copilot_score_packets")
    private = b"/private/var/folders/ab/" + b"Q" * 30 + b"/T/work/f.py"
    index = {"snapshots": [{"files": {sha(private)[:32]: {"path": private.decode()}}}]}
    documents = {"run/native/rewind-file-snapshots/index.json": canonical(index), "run/native/other.txt": b"key " + sha(private)[:32].encode()}
    temps = module.temp_aliases(documents)
    rekeys = module.rewind_rekeys(documents, temps)
    public = private.replace(b"ab/" + b"Q" * 30, b"xx/" + b"x" * 30)
    assert rekeys == {sha(private)[:32]: sha(public)[:32]} and len(next(iter(rekeys))) == len(next(iter(rekeys.values()))) == 32


def test_the_rejected_claude_set_fails_the_check():
    base = PREPARATION / "claude-cli-public-candidates-v7"
    packets = sorted(path for path in base.iterdir() if (path / "manifest.json").is_file())
    receipts = [json.loads((base / f"{packet.name}-receipt.json").read_bytes()) for packet in packets]
    with pytest.raises(UnboundDigestError, match="attempt.json"):
        check_public_packets(packets, receipts=receipts, allowlist=_sanitizer("sanitize_claude_score_packets").DIGEST_ALLOWLIST)
