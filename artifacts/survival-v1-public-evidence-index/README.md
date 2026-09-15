# Session-Bench v1 public evidence index

This directory is a deterministic index over the five existing sanitized
configuration packets. It contains repository-relative references and hashes;
it does not copy packet bytes. The referenced packets remain the source of
their own score, publication, and independent-reproduction fields.

The index verifies exactly `codex-cli`, `codex-desktop`, `claude-cli`,
`claude-desktop`, and `opencode-cli`, with three repetitions and 31 resolved
metrics per repetition. It does not calculate or publish a cross-configuration
rank. Independent reproduction and publication states are retained from each
packet's score document without relabeling.

The manifest and privacy receipt cover this index, while the verification scan
also covers every byte in all five referenced public packets. Raw native,
transcript, SQLite, credential, account, personal-history, and absolute-path
content is excluded by the nested packet contracts and checked again here.

Revalidate with:

`python3 scripts/build_public_evidence_index.py --verify --output artifacts/survival-v1-public-evidence-index`
