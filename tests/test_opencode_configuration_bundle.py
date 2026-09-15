"""Offline controls for the OpenCode public configuration packet boundary."""

from __future__ import annotations

import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from scripts.build_opencode_configuration_bundle import (
    _content_entries,
    _manifest_core,
    _privacy_findings,
    _validate_manifest,
    canonical_sha256,
)


def test_public_scan_catches_local_paths_credentials_and_addresses(tmp_path: Path) -> None:
    payload = tmp_path / "public.json"
    payload.write_text(
        '{"path":"/Users/private/project","authorization":"Bearer secret",'
        '"contact":"person@example.test"}\n',
        encoding="utf-8",
    )

    findings = _privacy_findings(tmp_path)

    assert findings == [
        "public.json:absolute_local_path",
        "public.json:email_address",
        "public.json:credential_assignment",
    ]


def test_manifest_digest_and_file_hashes_reject_tampering(tmp_path: Path) -> None:
    content = tmp_path / "runs/1/score-input/survival-evidence.json"
    content.parent.mkdir(parents=True)
    content.write_text('{"synthetic":true}\n', encoding="utf-8")
    bundle = SimpleNamespace(bundle_sha256="a" * 64, result_ids=("result-1",))
    core = _manifest_core(bundle=bundle, source_packages=[], files=_content_entries(tmp_path))
    manifest = {**core, "content_sha256": canonical_sha256(core)}
    (tmp_path / "bundle-manifest.json").write_text(
        json.dumps(manifest, sort_keys=True), encoding="utf-8"
    )

    _validate_manifest(tmp_path, manifest)

    content.write_text('{"synthetic":false}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="hash mismatch"):
        _validate_manifest(tmp_path, manifest)


@pytest.mark.skipif(sys.platform != "darwin", reason="source-denied profile is macOS-only")
def test_source_denied_profile_does_not_allow_broad_homebrew_reads() -> None:
    from scripts.build_opencode_configuration_bundle import _source_denied_profile

    profile = _source_denied_profile([Path("/private/tmp/read")], [Path("/private/tmp/write")])

    assert '(allow file-read* (subpath "/opt/homebrew"))' not in profile
    assert '(allow file-read* (subpath "/private/tmp/read"))' in profile
    assert '(allow file-write* (subpath "/private/tmp/write"))' in profile
