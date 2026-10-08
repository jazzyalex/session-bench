import builtins
import copy
import hashlib
import json
from pathlib import Path

import pytest

from session_bench.claude_desktop_source_discovery import (
    ClaudeDesktopSourceDiscoveryError,
    DISCOVERY_SCHEMA,
    INVENTORY_SCHEMA,
    SELECTION_SCHEMA,
    canonical_claude_desktop_roots,
    qualify_claude_desktop_stable_sources,
    verify_claude_desktop_source_discovery,
)


def test_canonical_roots_use_claude_home_override_or_default():
    home = Path("/Users/fixture")
    expected_desktop = home / "Library/Application Support/Claude/claude-code-sessions"
    assert canonical_claude_desktop_roots(home=home, environ={}) == {
        "transcript": home / ".claude/projects",
        "desktop_metadata": expected_desktop,
    }
    assert canonical_claude_desktop_roots(home=home, environ={"CLAUDE_HOME": "/Volumes/Claude"}) == {
        "transcript": Path("/Volumes/Claude/projects"),
        "desktop_metadata": expected_desktop,
    }
    with pytest.raises(ClaudeDesktopSourceDiscoveryError, match="absolute"):
        canonical_claude_desktop_roots(home=home, environ={"CLAUDE_HOME": "relative"})


def digest(value):
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False,
    ).encode()).hexdigest()


def entry(path, number, kind="other", size=10):
    return {
        "source_path": path,
        "resolved_path": path,
        "device": 10,
        "inode": number,
        "size_bytes": size,
        "ctime_ns": 1_000 + number,
        "mtime_ns": 2_000 + number,
        "kind": kind,
        "is_regular_file": True,
        "is_symlink": False,
    }


def inventory(run, repetition, phase, transcript_root, desktop_root, entries, captured_at):
    roots = []
    for root_id, source_root, filesystem_id in (
        ("claude-projects", transcript_root, "device:projects"),
        ("claude-desktop-sessions", desktop_root, "device:desktop"),
    ):
        root_entries = sorted(entries[root_id], key=lambda item: item["source_path"])
        roots.append({
            "root_id": root_id,
            "source_root": source_root,
            "resolved_root": source_root,
            "filesystem_id": filesystem_id,
            "complete": True,
            "aliases_detected": False,
            "entry_count": len(root_entries),
            "entries": root_entries,
        })
    return {
        "schema_version": INVENTORY_SCHEMA,
        "run_id": run,
        "repetition": repetition,
        "phase": phase,
        "captured_at_ns": captured_at,
        "metadata_only": True,
        "personal_history_content_read": False,
        "unrelated_content_read": False,
        "complete": True,
        "root_count": 2,
        "entry_count": sum(len(value) for value in entries.values()),
        "roots": roots,
    }


def evidence(repetition=1, prefix="fixture"):
    run = f"{prefix}-run-{repetition}"
    cli = f"cli-{prefix}-{repetition}"
    desktop = f"local_{prefix}_{repetition}"
    transcript_root = "/Users/example/Library/Application Support/Claude/projects"
    desktop_root = "/Users/example/Library/Application Support/Claude/sessions"
    old_transcript = entry(f"{transcript_root}/private-old-{repetition}.jsonl", 100 + repetition)
    old_desktop = entry(f"{desktop_root}/private-old-{repetition}.json", 200 + repetition)
    transcript = entry(f"{transcript_root}/fixture-{repetition}/{cli}.jsonl",
                       300 + repetition, "transcript", 101)
    metadata = entry(f"{desktop_root}/local_{prefix}_{repetition}.json",
                     400 + repetition, "desktop_metadata", 202)
    before_entries = {
        "claude-projects": [old_transcript],
        "claude-desktop-sessions": [old_desktop],
    }
    after_entries = {
        "claude-projects": [old_transcript, transcript],
        "claude-desktop-sessions": [old_desktop, metadata],
    }
    before = inventory(run, repetition, "before", transcript_root, desktop_root,
                       before_entries, 10)
    after = inventory(run, repetition, "after", transcript_root, desktop_root,
                      after_entries, 20)

    def selected(role, source):
        return {
            "schema_version": SELECTION_SCHEMA,
            "role": role,
            "run_id": run,
            "repetition": repetition,
            "cli_session_id": cli,
            "desktop_session_id": desktop,
            "source_path": source["source_path"],
            "device": source["device"],
            "inode": source["inode"],
            "size_bytes": source["size_bytes"],
            "ctime_ns": source["ctime_ns"],
            "mtime_ns": source["mtime_ns"],
            "sha256": hashlib.sha256(f"{role}-{repetition}".encode()).hexdigest(),
            "synthetic_fixture": True,
        }

    return {
        "run_id": run,
        "repetition": repetition,
        "cli_session_id": cli,
        "desktop_session_id": desktop,
        "before_inventory": before,
        "after_inventory": after,
        "selected_transcript": selected("transcript", transcript),
        "selected_desktop_metadata": selected("desktop_metadata", metadata),
    }


def test_isolates_exact_pair_without_reading_files_or_disclosing_unrelated_paths(monkeypatch):
    inputs = evidence()
    unrelated_paths = [
        root["entries"][0]["source_path"]
        for root in inputs["before_inventory"]["roots"]
    ]
    monkeypatch.setattr(
        builtins, "open",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("filesystem read")),
    )
    receipt = verify_claude_desktop_source_discovery(**inputs)
    encoded = json.dumps(receipt, sort_keys=True)
    assert receipt["schema_version"] == DISCOVERY_SCHEMA
    assert receipt["isolated_pair"] is True
    assert {item["source_path"] for item in receipt["selected_artifacts"]} == {
        inputs["selected_transcript"]["source_path"],
        inputs["selected_desktop_metadata"]["source_path"],
    }
    assert all(path not in encoded for path in unrelated_paths)
    assert all("source_root" not in root for root in receipt["root_summaries"])
    assert all(root["unrelated_entry_count"] == 1 for root in receipt["root_summaries"])


@pytest.mark.parametrize("mutation,match", [
    (lambda data: data["before_inventory"].update(complete=False), "unsafe or incomplete"),
    (lambda data: data["before_inventory"].update(personal_history_content_read=True), "unsafe or incomplete"),
    (lambda data: data["after_inventory"]["roots"][0].update(
        resolved_root="/alias/Claude/projects"), "aliased"),
    (lambda data: data["after_inventory"]["roots"][0]["entries"][0].update(
        inode=data["after_inventory"]["roots"][1]["entries"][0]["inode"]), "file aliases"),
    (lambda data: data["selected_transcript"].update(run_id="other-run"), "identity mismatch"),
    (lambda data: data["selected_desktop_metadata"].update(cli_session_id="other-cli"), "identity mismatch"),
    (lambda data: data["selected_transcript"].update(synthetic_fixture=False), "fixture-scoped"),
    (lambda data: data["after_inventory"]["roots"][0]["entries"].append(
        entry("/Users/example/Library/Application Support/Claude/projects/unexpected.jsonl", 999)),
     "entry population mismatch"),
])
def test_fails_closed_on_unsafe_incomplete_aliased_or_mismatched_scope(mutation, match):
    inputs = evidence()
    mutation(inputs)
    with pytest.raises(ClaudeDesktopSourceDiscoveryError, match=match):
        verify_claude_desktop_source_discovery(**inputs)


def test_allows_extra_changed_entry_without_selecting_it():
    inputs = evidence()
    root = inputs["after_inventory"]["roots"][0]
    extra = entry(root["source_root"] + "/unexpected.jsonl", 999)
    root["entries"].append(extra)
    root["entries"].sort(key=lambda item: item["source_path"])
    root["entry_count"] += 1
    inputs["after_inventory"]["entry_count"] += 1
    receipt = verify_claude_desktop_source_discovery(**inputs)
    assert extra["source_path"] not in json.dumps(receipt)
    assert receipt["isolated_pair"] is True


def test_isolates_exact_session_with_unrelated_concurrent_metadata_churn(monkeypatch):
    inputs = evidence()
    root = inputs["after_inventory"]["roots"][1]
    # An existing unrelated session changed while another unrelated session was
    # created. Both remain part of the complete inventory without content reads.
    existing_index = next(index for index, item in enumerate(root["entries"])
                          if "private-old" in item["source_path"])
    existing = root["entries"][existing_index]
    existing["kind"] = "desktop_metadata"
    inputs["before_inventory"]["roots"][1]["entries"][0]["kind"] = "desktop_metadata"
    # The fixture shares old entries between inventories; detach the after row.
    root["entries"][existing_index] = copy.deepcopy(existing)
    root["entries"][existing_index]["mtime_ns"] += 1
    extra = entry(root["source_root"] + "/local_other.json", 999, "desktop_metadata")
    root["entries"].append(extra)
    root["entries"].sort(key=lambda item: item["source_path"])
    root["entry_count"] += 1
    inputs["after_inventory"]["entry_count"] += 1
    monkeypatch.setattr(
        builtins, "open",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("filesystem read")),
    )
    receipt = verify_claude_desktop_source_discovery(**inputs)
    assert receipt["complete_inventories"] is True
    assert receipt["isolated_pair"] is True
    assert {item["source_path"] for item in receipt["selected_artifacts"]} == {
        inputs["selected_transcript"]["source_path"],
        inputs["selected_desktop_metadata"]["source_path"],
    }
    summary = next(item for item in receipt["root_summaries"]
                   if item["root_id"] == "claude-desktop-sessions")
    assert (summary["before_entry_count"], summary["after_entry_count"],
            summary["unrelated_entry_count"]) == (1, 3, 2)
    assert existing["source_path"] not in json.dumps(receipt)
    assert extra["source_path"] not in json.dumps(receipt)


def test_rejects_duplicate_changed_desktop_session_filename():
    inputs = evidence()
    root = inputs["after_inventory"]["roots"][1]
    extra = entry(root["source_root"] + "/duplicate/"
                  + inputs["desktop_session_id"] + ".json", 999, "desktop_metadata")
    root["entries"].append(extra)
    root["entries"].sort(key=lambda item: item["source_path"])
    root["entry_count"] += 1
    inputs["after_inventory"]["entry_count"] += 1
    with pytest.raises(ClaudeDesktopSourceDiscoveryError, match="unique changed Desktop session filename"):
        verify_claude_desktop_source_discovery(**inputs)


def test_rejects_selected_desktop_metadata_with_wrong_session_filename():
    inputs = evidence()
    root = inputs["after_inventory"]["roots"][1]
    selected = inputs["selected_desktop_metadata"]
    source = next(item for item in root["entries"]
                  if item["source_path"] == selected["source_path"])
    wrong_path = root["source_root"] + "/local_other.json"
    source.update(source_path=wrong_path, resolved_path=wrong_path)
    selected["source_path"] = wrong_path
    root["entries"].sort(key=lambda item: item["source_path"])
    with pytest.raises(ClaudeDesktopSourceDiscoveryError, match="unique changed Desktop session filename"):
        verify_claude_desktop_source_discovery(**inputs)


def test_disappeared_unrelated_entry_does_not_invalidate_unique_pair():
    inputs = evidence()
    root = inputs["after_inventory"]["roots"][0]
    disappeared = next(item["source_path"] for item in root["entries"]
                       if "private-old" in item["source_path"])
    root["entries"] = [item for item in root["entries"]
                       if item["source_path"] != disappeared]
    root["entry_count"] -= 1
    inputs["after_inventory"]["entry_count"] -= 1

    receipt = verify_claude_desktop_source_discovery(**inputs)
    assert {item["source_path"] for item in receipt["selected_artifacts"]} == {
        inputs["selected_transcript"]["source_path"],
        inputs["selected_desktop_metadata"]["source_path"],
    }
    assert disappeared not in json.dumps(receipt)
    summary = next(item for item in receipt["root_summaries"]
                   if item["root_id"] == "claude-projects")
    assert (summary["before_entry_count"], summary["after_entry_count"],
            summary["unrelated_entry_count"]) == (1, 1, 0)


def test_deleted_entry_does_not_count_as_selected_pair():
    inputs = evidence()
    root = inputs["after_inventory"]["roots"][0]
    root["entries"] = []
    root["entry_count"] = 0
    inputs["after_inventory"]["entry_count"] -= 2

    with pytest.raises(ClaudeDesktopSourceDiscoveryError, match="selected pair is not among"):
        verify_claude_desktop_source_discovery(**inputs)


def test_rejects_duplicate_path_and_noncanonical_order():
    inputs = evidence()
    root = inputs["after_inventory"]["roots"][0]
    root["entries"].reverse()
    with pytest.raises(ClaudeDesktopSourceDiscoveryError, match="canonically ordered"):
        verify_claude_desktop_source_discovery(**inputs)


def test_three_distinct_discoveries_prove_stable_hashed_roots_and_selected_locations():
    receipts = [verify_claude_desktop_source_discovery(**evidence(r)) for r in (1, 2, 3)]
    qualified = qualify_claude_desktop_stable_sources(receipts)
    assert qualified["evidence_complete"] is True
    assert [item["repetition"] for item in qualified["observations"]] == [1, 2, 3]
    assert {item["role"] for item in qualified["stable_roots"]} == {
        "transcript", "desktop_metadata",
    }
    assert all(item["observed_repetitions"] == 3 for item in qualified["stable_roots"])


def test_stability_fails_closed_on_changed_root_reused_session_or_tampering():
    receipts = [verify_claude_desktop_source_discovery(**evidence(r)) for r in (1, 2, 3)]

    changed_root = copy.deepcopy(receipts)
    artifact = changed_root[2]["selected_artifacts"][0]
    old_root = artifact["source_path"][:-len("/" + artifact["relative_path"])]
    new_root = "/Volumes/alternate/Claude/projects"
    artifact["source_path"] = new_root + "/" + artifact["relative_path"]
    artifact["source_root_sha256"] = hashlib.sha256(new_root.encode()).hexdigest()
    transcript_summary = next(
        item for item in changed_root[2]["root_summaries"]
        if item["root_id"] == "claude-projects"
    )
    assert old_root != new_root
    transcript_summary["source_root_sha256"] = artifact["source_root_sha256"]
    body = {key: value for key, value in changed_root[2].items() if key != "proof_sha256"}
    changed_root[2]["proof_sha256"] = digest(body)
    with pytest.raises(ClaudeDesktopSourceDiscoveryError, match="not stable"):
        qualify_claude_desktop_stable_sources(changed_root)

    reused = copy.deepcopy(receipts)
    reused[2]["cli_session_id"] = reused[1]["cli_session_id"]
    body = {key: value for key, value in reused[2].items() if key != "proof_sha256"}
    reused[2]["proof_sha256"] = digest(body)
    with pytest.raises(ClaudeDesktopSourceDiscoveryError, match="reuses"):
        qualify_claude_desktop_stable_sources(reused)

    tampered = copy.deepcopy(receipts)
    tampered[0]["selected_artifacts"][0]["source_path"] += ".forged"
    with pytest.raises(ClaudeDesktopSourceDiscoveryError, match="integrity"):
        qualify_claude_desktop_stable_sources(tampered)
