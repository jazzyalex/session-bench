# Closed native decoder replay

`session_bench.native_replay` builds a private package from an explicitly named
copied Codex or Claude JSONL bundle. It includes native bytes, the decoder and
its fixed Python dependency closure, a runner, exact inventory hashes, and an
expected decoded digest. It never searches an account home or launches a model.

The host verifies the closed file inventory before executing the packaged
runner. Rechecks execute an immutable copy using isolated Python, without
repository or site-package imports. Tampered runner/decoder/native bytes,
undeclared files, symlinks and unsafe inventory paths are rejected.

Example using the repository's synthetic Codex fixture:

```python
from pathlib import Path
import hashlib
from session_bench.native_replay import build_native_replay_package, replay_native_package

destination = Path("artifacts/example-native-replay")
manifest = build_native_replay_package(
    Path("tests/fixtures/codex-cli-native-0154"), destination,
    configuration_id="codex-cli", repetition=1,
)
trusted_digest = hashlib.sha256((destination / "manifest.json").read_bytes()).hexdigest()
receipt = replay_native_package(destination, expected_manifest_sha256=trusted_digest)
assert receipt["decode_sha256"] == manifest["expected_decode_sha256"]
```

The destination must be new. Do not overwrite an existing evidence package
when changing a decoder; create a successor with its own manifest and record
the old manifest hash as correction provenance.

Retain the trusted manifest digest separately at creation or review time.
Internal hashes alone prove consistency with a manifest, not the authenticity
of bundled executable code. An externally supplied package needs a trusted
digest before execution; recomputing that digest from the same untrusted input
does not establish trust.

## Current receipts

The local preparation directory
`artifacts/v1-expanded-preparation/native-replay` contains 12 packages and
receipts: three each for Codex CLI, Codex Desktop, Claude CLI and Claude
Desktop. All 12 decoded successfully with their packaged runtime snapshots.
The source inputs were explicitly named retained synthetic benchmark captures.
The first two Claude Desktop inputs are their `correction-1` runs.

These runtime snapshots preceded the new complete JSONL density inventory and
the later Claude decoder provenance extension. They remain immutable; they
are not evidence that every current working-tree decoder revision was replayed.

## What these receipts establish

- The named copied native bytes decode under the exact packaged source.
- The runtime can execute in a fresh temporary directory without repository
  imports, and yields the packaged expected decode digest.
- The source package remains unchanged during recheck.

They are **native-decoder checks only**. They do not establish full native-root
acquisition, workload/observer correctness, loss-control performance, full
31-metric score reproduction, OS-enforced isolation, independent reproduction,
or public safety. The package explicitly retains `public_safe=false` and
`independent_reproduction=false`. Its native bytes may contain private paths.

The Codex decoder's default workload matching is used by this primitive.
A full score replay must additionally bind each run's actual frozen workload,
observer, portability evidence and scorer; an equal native-decoder digest
cannot replace those steps. Claude Desktop full-family companions also require
their own completeness/reconstruction proof beyond transcript decoding.

Before handing a package to an independent reproducer, complete those inputs,
bind the corrected evaluator source, establish OS-enforced isolation, and
prepare and review a separately labelled public native derivative. See
[independent-reproduction.md](independent-reproduction.md).
