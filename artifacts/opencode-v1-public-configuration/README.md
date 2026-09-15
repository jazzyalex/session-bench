# OpenCode CLI v1 public configuration packet

This is the sanitized three repetition configuration packet for OpenCode CLI.
It is derived from three immutable `missing_decoder_runtime` correction packages
and contains the exact public score inputs for all 31 v1 metrics.

## Result

- Configuration: OpenCode CLI 1.18.30 using `opencode/muse-spark-1.3-contributor-free`
- Three repetition mean: **83.3/100** (exact arithmetic `333/4`)
- Repetition range: **83.3–83.3**
- Offline verification: **fully_reproduced** for this configuration packet
- Independent reproduction: **not established** (`independent_reproduction: false`)
- Five-angle categories: record_fidelity 20.3, causality_context 20.0, usage_attribution 12.0, portability_openness 18.0, durability_signal 13.0

An internal report-card line (not cleared for publication) would read: “OpenCode CLI
scored 83.3/100 on Session-Bench v1's five-angle, 31-metric configuration packet across
three corrected runs.” This is a configuration-level local evidence result. The
independent-reproduction and five-configuration cohort gates remain closed, so it is
not a public v1 score, leaderboard rank, or general model-quality claim.

## Evidence boundary

Each repetition includes a sanitized survival evidence input, a qualified 12-metric
format evidence input, the scorer's 31-metric output, and a source-denied replay
receipt. The replay copied the correction package into a temporary directory and
ran only its bundled decoder, comparator, scorer, package verifier, and recheck
runner. Original package bytes, the vendor executable, and network access were
denied. The selected loss control removed the observed R2 response and changed
visible-response accuracy from 2/2 to 1/2 in every repetition.

Raw SQLite/WAL/SHM files, replay-runtime source, original workspace paths, and
account data are withheld. The `semantic` directories are redacted derivatives;
they are exact public evidence inputs but are not raw native packages.

The local source-denied receipt intentionally says `independent_reproduction: false`.
The separate audit used the same host and filesystem, so it corroborates the
replay without qualifying an independent operator or environment.

## Verification

The canonical binding is in `configuration-bundle.json`; its digest is bound by
`configuration-evidence.json` and `bundle-manifest.json`. The strict scorer input
is the pair of files under each `runs/<n>/score-input/` directory. Run
`python3 scripts/build_opencode_configuration_bundle.py --verify` after copying
this directory into a checkout to revalidate the hashes, 31 cells, score, privacy
scan, and global-cohort boundary.

`bundle-manifest.json` is an immutable manifest over all packet content except
itself, this README, and the source-denied receipt, which bind the manifest after
the content digest is known.
