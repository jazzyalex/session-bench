"""Verify retained metadata-only Codex CLI capture root receipts.

This checks archived capture provenance, not a fresh inventory of personal roots.
"""
import hashlib
import json
from pathlib import Path
import re

FILES = ("normal-root-receipt.json", "calibration-receipt.json", "decode.json")
# Where each file lives under one retained ``codex-cli-eval-N`` run directory.
SOURCES = {
    "normal-root-receipt.json": "capture/requalification-v1/capture/normal-root-receipt.json",
    "calibration-receipt.json": "capture/calibration-receipt.json",
    "decode.json": "capture/requalification-v1/capture/canonical-private/decode.json",
}
_SELECTED_PATH = re.compile(r"^\d{4}/\d{2}/\d{2}/rollout-[^/]+-(?P<session>[0-9a-f-]{36})\.jsonl$")


def verify_codex_cli_root_evidence(root: Path):
    """Return three root metric rows from a copied receipt directory."""
    root = Path(root)
    rows = []
    sessions = set()
    for repetition in (1, 2, 3):
        folder = root / f"repetition-{repetition}"
        raw = {name: (folder / name).read_bytes() for name in FILES}
        normal, calibration, decode = (json.loads(raw[name]) for name in FILES)

        def require(condition, message):
            if not condition:
                raise ValueError(f"Codex CLI root repetition {repetition}: {message}")

        selected_path = normal.get("selected_path")
        match = _SELECTED_PATH.fullmatch(selected_path) if isinstance(selected_path, str) else None
        require(match is not None and match.group("session") == normal.get("session_id"), "selected path is not session-bound")
        capture = normal.get("capture", {})
        require(normal.get("schema_version") == "1.0-codex-cli-normal-root-requalification"
                and normal.get("configuration_id") == "codex-cli" and normal.get("root") == "CODEX_HOME/sessions"
                and normal.get("metadata_only_before_after") is True
                and normal.get("preexisting_native_content_opened") is False
                and normal.get("preexisting_native_paths_disclosed") is False
                and capture.get("offline_decode_equal") is True and capture.get("selected_loss_detected") is True,
                "normal-root receipt boundary is incomplete")
        historical = normal.get("historical_proof", {})
        require(historical.get("receipt_sha256") == hashlib.sha256(raw["calibration-receipt.json"]).hexdigest()
                and type(historical.get("before_file_count")) is int
                and historical.get("after_r1_file_count") == historical["before_file_count"] + 1
                and historical.get("after_r2_file_count") == historical["after_r1_file_count"]
                and calibration.get("thread_id") == normal.get("session_id"),
                "historical root transition is not closed")
        artifacts = decode.get("artifacts")
        require(isinstance(artifacts, list) and len(artifacts) == 1 and artifacts[0].get("path") == selected_path
                and artifacts[0].get("sha256") == historical.get("selected_native_sha256"),
                "selected native file is not hash-bound")
        size = artifacts[0].get("size_bytes")
        selected = normal.get("current_selected_metadata", {})
        require(selected.get("root") == "CODEX_HOME/sessions" and selected.get("file_count") == 1
                and selected.get("total_size_bytes") == size and selected.get("metadata_only") is True
                and selected.get("preexisting_paths_disclosed") is False,
                "selected native metadata is not closed")
        quiescence = normal.get("quiescence", {})
        observed = quiescence.get("observed")
        require(quiescence.get("stable") is True and quiescence.get("checks") == 2
                and isinstance(observed, list) and len(observed) == 2 and observed[0] == observed[1]
                and len(observed[0]) == 1 and observed[0][0].get("relative_path") == selected_path
                and observed[0][0].get("filesystem_id") == historical.get("selected_filesystem_id")
                and observed[0][0].get("size_bytes") == size,
                "selected native file was not quiescent")
        sessions.add(normal["session_id"])
        rows.append({"repetition": repetition,
                     "root_locator": "CODEX_HOME/sessions/YYYY/MM/DD/rollout-<session-id>.jsonl",
                     "discovery_mode": "metadata_safe_normal_root", "personal_history_scanned": False})
    if len(sessions) != 3:
        raise ValueError("Codex CLI root repetitions are not distinct sessions")
    return rows
