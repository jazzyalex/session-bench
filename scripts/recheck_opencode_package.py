#!/usr/bin/env python3
"""Recheck one closed OpenCode evidence package without launching a model.

This is a local reproduction aid, not an independent-reproduction badge or a
public v1 score. It verifies the package, decodes a temporary copy of its
native SQLite family, and reruns the frozen observer/native comparison.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile

# A closed package is immutable.  Imports must not add __pycache__ files to the
# runtime boundary before its manifest is checked.
sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.build_live_survival_result import _read, _verify_and_score, _verify_package  # noqa: E402
from session_bench.adapters.opencode_decoder import decode_opencode_bundle  # noqa: E402
from session_bench.live_metric_comparator import compare_survival_run  # noqa: E402
from session_bench.survival_metrics import score_run  # noqa: E402


def _normalized_decode(value: dict) -> bytes:
    """Normalize the copied-bundle label without importing capture machinery."""
    normalized = deepcopy(value)
    bundle = normalized.get("bundle")
    if isinstance(bundle, dict):
        bundle["path"] = "bundle"
    return json.dumps(
        normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _verify_runtime(runtime: Path) -> dict:
    """Verify the package-local source closure before executing it."""
    if runtime.is_symlink() or not runtime.is_dir():
        raise ValueError("package lacks a closed replay runtime")
    if any(path.is_symlink() for path in runtime.rglob("*")):
        raise ValueError("replay runtime must not contain symlinks")
    manifest_path = runtime / "manifest.json"
    manifest = _read(manifest_path)
    if manifest.get("schema_version") != 1 or not isinstance(manifest.get("files"), list):
        raise ValueError("replay runtime manifest is malformed")
    listed = manifest["files"]
    expected_paths = {str(item.get("path")) for item in listed if isinstance(item, dict)}
    actual_paths = {
        path.relative_to(runtime).as_posix()
        for path in runtime.rglob("*")
        if path.is_file() and path.name != "manifest.json"
    }
    if expected_paths != actual_paths or len(expected_paths) != len(listed):
        raise ValueError("replay runtime file boundary mismatch")
    for item in listed:
        if not isinstance(item, dict):
            raise ValueError("replay runtime entry is malformed")
        relative = item.get("path")
        if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
            raise ValueError("replay runtime path is malformed")
        path = runtime / relative
        if not path.is_file() or path.is_symlink():
            raise ValueError("replay runtime file is missing")
        actual = path.read_bytes()
        if hashlib.sha256(actual).hexdigest() != item.get("sha256") or len(actual) != item.get("size_bytes"):
            raise ValueError(f"replay runtime artifact mismatch: {relative}")
    return manifest


def recheck(package_dir: Path) -> dict[str, object]:
    """Recompute one copied package; leave the supplied directory untouched."""
    if package_dir.is_symlink() or not package_dir.is_dir():
        raise ValueError("package must be an ordinary directory")
    if any(path.is_symlink() for path in package_dir.rglob("*")):
        raise ValueError("package must not contain symlinks")
    runtime = package_dir / "replay-runtime"
    _verify_runtime(runtime)
    if ROOT.resolve() != runtime.resolve():
        raise ValueError("recheck must execute from the package-local replay runtime")
    stored_run, evidence, summary, measurement, package = _verify_and_score(package_dir)
    selected_session = summary.get("session_id")
    if not isinstance(selected_session, str) or not selected_session:
        raise ValueError("package summary lacks the selected session ID")
    fresh_decode = decode_opencode_bundle(
        package_dir / "native-bundle", session_id=selected_session
    )
    stored_decode = _read(package_dir / "decoded.json")
    if _normalized_decode(fresh_decode) != _normalized_decode(stored_decode):
        raise ValueError("fresh native decode differs from stored decoded representation")
    fresh_measurement = compare_survival_run(
        _read(package_dir / "observer.json"),
        fresh_decode,
        _read(package_dir / "portability-receipt.json"),
        configuration_id=evidence["configuration_id"],
        repetition=evidence["repetition"],
    )
    if fresh_measurement != measurement:
        raise ValueError("fresh native comparison differs from recorded measurement")
    fresh_run = score_run(fresh_measurement)
    if fresh_run.display() != stored_run.display():
        raise ValueError("fresh survival score differs from evidence-bound score")
    return {
        "scope": "local_copied_package_recheck_only",
        "independent_reproduction": False,
        "configuration_id": evidence["configuration_id"],
        "repetition": evidence["repetition"],
        "package_id": package["package_id"],
        "package_digest": package["package_digest"],
        "result_id": package["result_id"],
        "session_id": selected_session,
        "survival_overall": fresh_run.display()["overall"],
        "fresh_decode_sha256": hashlib.sha256(_normalized_decode(fresh_decode)).hexdigest(),
    }


def damage_control(package_dir: Path) -> dict[str, object]:
    """Prove a copied native R2 response loss changes frozen measurement output."""
    if package_dir.is_symlink() or not package_dir.is_dir():
        raise ValueError("package must be an ordinary directory")
    runtime = package_dir / "replay-runtime"
    _verify_runtime(runtime)
    if ROOT.resolve() != runtime.resolve():
        raise ValueError("damage control must execute from the package-local replay runtime")
    evidence = _read(package_dir / "evidence.json")
    summary = _read(package_dir / "summary.json")
    observer = _read(package_dir / "observer.json")
    response_events = [
        event for event in observer.get("events", [])
        if isinstance(event, dict)
        and event.get("id") == "response-r2"
        and isinstance(event.get("fields"), dict)
    ]
    if len(response_events) != 1:
        raise ValueError("package has no unique observed R2 response")
    canary = response_events[0]["fields"].get("canary")
    if not isinstance(canary, str) or not canary:
        raise ValueError("observed R2 response has no canary")
    selected_session = summary.get("session_id")
    if not isinstance(selected_session, str) or not selected_session:
        raise ValueError("package summary lacks the selected session ID")
    with tempfile.TemporaryDirectory(prefix="session-bench-opencode-damage-") as raw:
        damaged = Path(raw) / "native-bundle"
        shutil.copytree(package_dir / "native-bundle", damaged, symlinks=False)
        connection = sqlite3.connect(damaged / "opencode.db")
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            rows = connection.execute(
                """
                SELECT part.id
                FROM part JOIN message ON message.id = part.message_id
                WHERE part.session_id = ?
                  AND part.data LIKE ?
                  AND json_extract(message.data, '$.role') = 'assistant'
                ORDER BY part.id
                """,
                (selected_session, f"%{canary}%"),
            ).fetchall()
            if not rows:
                raise ValueError("selected R2 canary is absent from copied native records")
            selected_row = str(rows[0][0])
            connection.execute("DELETE FROM part WHERE id = ?", (selected_row,))
            connection.commit()
            if not all((damaged / name).is_file() for name in ("opencode.db", "opencode.db-wal", "opencode.db-shm")):
                raise ValueError("damage control did not retain the declared native family")
            decoded = decode_opencode_bundle(damaged, session_id=selected_session)
        finally:
            connection.close()
        if canary in json.dumps(decoded.get("responses", []), ensure_ascii=False):
            raise ValueError("damaged native copy still reconstructs the selected R2 response")
        damaged_measurement = compare_survival_run(
            observer,
            decoded,
            _read(package_dir / "portability-receipt.json"),
            configuration_id=evidence["configuration_id"],
            repetition=evidence["repetition"],
        )
        intact_measurement = _read(package_dir / "measurement.json")
        if damaged_measurement == intact_measurement:
            raise ValueError("damage control did not change the frozen measurement")
        response_metric = next(
            item for item in damaged_measurement["metrics"] if item["id"] == "work.visible_responses"
        )
        intact_response_metric = next(
            item for item in intact_measurement["metrics"] if item["id"] == "work.visible_responses"
        )
        if not (
            isinstance(response_metric.get("correct"), int)
            and isinstance(intact_response_metric.get("correct"), int)
            and response_metric["correct"] < intact_response_metric["correct"]
        ):
            raise ValueError("damage control did not report selected R2 response loss")
        return {
            "scope": "local_copied_package_damage_control_only",
            "independent_reproduction": False,
            "package_id": summary["package_id"],
            "selected_observer_id": "response-r2",
            "selected_native_table": "part",
            "selected_native_row_id": selected_row,
            "selected_canary": canary,
            "loss_state": "reduced_visible_response_accuracy",
            "intact_correct": intact_response_metric["correct"],
            "damaged_correct": response_metric["correct"],
        }


def _delegate_to_copied_runtime(package_dir: Path, *, damage: bool) -> int:
    """Copy the whole package, then execute only its bundled runner and source."""
    _verify_runtime(package_dir / "replay-runtime")
    with tempfile.TemporaryDirectory(prefix="session-bench-opencode-recheck-") as raw:
        copied = Path(raw) / "package"
        shutil.copytree(package_dir, copied, symlinks=False)
        runner = copied / "replay-runtime/scripts/recheck_opencode_package.py"
        environment = dict(os.environ)
        environment.pop("PYTHONPATH", None)
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        command = [sys.executable, str(runner), "--package", str(copied), "--execute-in-runtime"]
        if damage:
            command.append("--damage-control")
        completed = subprocess.run(
            command,
            cwd=copied / "replay-runtime",
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if completed.stdout:
            print(completed.stdout, end="")
        if completed.stderr:
            print(completed.stderr, end="", file=sys.stderr)
        return completed.returncode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", required=True, type=Path)
    parser.add_argument("--execute-in-runtime", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--damage-control", action="store_true")
    args = parser.parse_args(argv)
    package = args.package.resolve(strict=False)
    if not args.execute_in_runtime:
        try:
            return _delegate_to_copied_runtime(package, damage=args.damage_control)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            print(f"package recheck refused: {exc}", file=sys.stderr)
            return 1
    try:
        result = damage_control(package) if args.damage_control else recheck(package)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"package recheck refused: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
