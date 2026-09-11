# L0 implementation re-review — Sol Extra High

Review target: commit `6105c285b29087409c25d3a59ce61e9929eaf238` on connected GitHub.

The reviewer verified GPT-5.6 Sol at Extra High effort and returned **NOT READY** with no P0 findings, three P1 findings, and one P2 finding. It closed deterministic event identity and the frozen intact-to-damaged control. It also closed the original new-session proof, subject to the P2 copy race below.

The remaining findings were:

1. Reusing an attempt ID replaced its previous ledger row instead of rejecting the duplicate and preserving append-only history.
2. The controller treated `finish-current-only` after an unreadable post-launch quota observation as an interruption rather than completing the launched attempt and forbidding retries.
3. Native-live binding lacked `scenario_id` and stored only an opaque resolved-configuration digest, without an inventoried preimage that offline validation could recompute.
4. Final path re-stat and `shutil.copy2` left a pathname replacement window between verification and copy.

The reviewer again stated that live F0 remains correctly blocked at 15% weekly usage against the 13% stop and while every plugin-managed MCP cannot be disabled through the reviewed non-mutating configuration path.

The following hardening revision rejects duplicate attempt IDs before launch, retains explicit quota/retry state, binds scenario identity, inventories and recomputes the resolved configuration, and copies from a no-follow descriptor whose `fstat` matches the selected candidate.
