# Press and outreach templates

Status: **UNPUBLISHED drafts — review-candidate only.**
No sending or distribution is authorized.
Do not claim publication, full independent native reproduction, Oracle approval, crash results, or final rankings.

## Release template (draft, do not publish)

**Session-Bench v1 review-candidate adds Desktop surfaces with review-candidate scores and local copied-root evidence**

[DATE] — Session-Bench has prepared an unpublished v1 review-candidate comparing Codex CLI, Codex Desktop, OpenCode CLI, Claude Desktop, and Claude CLI across Record fidelity (30), Causality & context (20), Usage & attribution (15), Portability & openness (20), and Durability & signal (15).

The kit applies one controlled task with a failing test, corrected requirement, edit, and final test. Review-candidate order: Codex CLI 87.0 (86.9–87.0; 21.0/20.0/12.0/20.0/14.0) and Codex Desktop 87.0 (87.0–87.0; 21.0/20.0/12.0/20.0/14.0) tie; then OpenCode CLI 83.3 (20.3/20.0/12.0/18.0/13.0), Claude Desktop 82.8 (25.0/14.8/12.0/18.0/13.0), and Claude CLI 81.0 (30.0/20.0/0.0/18.0/13.0). This order is not yet a public leaderboard.

All 15 runs have 31/31 metrics resolved with local copied-root replay/equality/loss controls. The public evidence index covers exactly 5 configs and 177 files with zero privacy findings. Exact model labels: Codex gpt-5.6-sol; Claude CLI `claude-sonnet-5[1m]`; Claude Desktop `claude-opus-5`; OpenCode `opencode/muse-spark-1.3-contributor-free`.

Verified descriptive findings in this kit:

- Codex CLI and Desktop tie at 87.0 with nearly identical five-bar profiles.
- Claude CLI holds the highest Fidelity (30/30) and Causality (20/20) but scores Usage/Attribution 0/15 because its native model label strips `[1m]` versus observed `claude-sonnet-5[1m]`, and native usage contradicts independently observed CLI stream counts.
- Desktop is a measured surface: Codex Desktop ties its CLI; Claude Desktop at 82.8 scores 1.8 above Claude CLI at 81.0, with lower fidelity/causality because its native record batches observed actions.
- OpenCode at 83.3 holds Causality 20/20, with public semantic/score verification; native public replay remains source-attested because raw SQLite/runtime are withheld.

The **If you're building on session files** table is generated/pending in this kit. This draft makes no use-case recommendation until the generated recommendation records are attached. Vendor report cards list evidence-linked **To pass, fix** items only where generated records support them.

V1.0 evolves the historical v0.4 CLI field study; it does not rescore it. No crash behavior is reported here. The benchmark measures saved records, not coding ability or untested product surfaces. No full independent native reproduction or Oracle approval is claimed.

Report: [REPORT_URL]
Method: [METHOD_URL]
Evidence: [EVIDENCE_URL]

## Short pitch / press email (draft, do not send)

Subject: Unpublished review-candidate: what five coding-agent surfaces record

Hi [NAME],

Session-Bench v1 review-candidate (unpublished, not a public leaderboard) compares Codex CLI, Codex Desktop, OpenCode CLI, Claude Desktop, and Claude CLI using one controlled coding task and a five-angle report card.

Review-candidate: Codex CLI 87.0 (86.9–87.0) ties Codex Desktop 87.0 (87.0–87.0); OpenCode CLI 83.3; Claude Desktop 82.8; Claude CLI 81.0. All 15 runs: 31/31 metrics resolved with local copied-root controls. Evidence index: 5 configs, 177 files, zero privacy findings. Example: Claude CLI keeps Fidelity 30/30 and Causality 20/20 but scores Usage 0/15 on the model-label and stream-count gaps above. Generated local recommendations remain unpublished.

The draft includes native evidence, result IDs, and limits rather than product-wide recommendations.

[REPORT_URL]

Alexander

## Maintainer evidence note (draft, do not send)

Subject: Session-Bench review-candidate evidence for [SURFACE / BUILD / RESULT ID]

Hi [NAME],

The unpublished review-candidate result for [SURFACE/BUILD/DATE] reports a bounded fact from the kit above (e.g. OpenCode CLI 83.3 with 20.3/20.0/12.0/18.0/13.0; Claude CLI Usage 0/15 on the stated gaps). Its observer record, native locator, rubric state, and any generated **To pass, fix** item are here: [EVIDENCE_URL].

Notes: OpenCode native public replay is source-attested (raw SQLite/runtime withheld). Codex model label is gpt-5.6-sol. No full independent native reproduction, Oracle approval, crash, publication, or final-rank claim is made.

If the artifact interpretation or declared root is incomplete, an artifact-level correction would help. The frozen correction rules apply to every surface.

Alexander

Every external statement must come from a final immutable result. Do not call the work peer-reviewed, definitive, industry-standard, or independently reproduced unless the exact badge receipt supports that term.

## Final-release substitution checklist (keep until release)

- [ ] Replace `[DATE]` with final release date only after authorization.
- [ ] Replace `[REPORT_URL]`, `[METHOD_URL]`, `[EVIDENCE_URL]` with final immutable URLs.
- [ ] Replace review-candidate order language with final rank language only from final badge/result IDs.
- [ ] Confirm OpenCode CLI, Claude Desktop, Claude CLI ranges from final machine-readable result.
- [ ] Confirm generated recommendation + **To pass, fix** records (or keep pending/`no_recommendation`).
- [ ] Do not add publication, independent-reproduction, Oracle-approval, crash, or final-rank claims unless the final package supports them.
