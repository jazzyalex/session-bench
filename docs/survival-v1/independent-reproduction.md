# Independent reproduction packet

This packet is for a second operator or a separate environment. It is not a
local recheck and it must not use the original native root, a signed-in vendor
application, network access, or the implementation operator's generated
outputs as an oracle.

## Input handed to the reproducer

Provide one immutable sanitized evidence directory containing its manifest,
observer record, decoded representation, evaluator configuration, the declared
copied native package, and the exact decoder source file named and hashed by
the manifest. The hand-off names the exact package digest,
surface/build/configuration tuple, decoder revision, evaluator revision, and
the expected result digest. It contains no account data, personal session
history, credentials, or original root paths.

## Reproduction steps

1. Verify the supplied package digest and file inventory.
2. Copy the supplied directory into a new local temporary directory.
3. Deny the original package path, vendor executable paths, and network to the
   decoder/evaluator process.
4. Verify that the decoder-source digest matches the manifest, then run the
   package's documented recheck command. For an OpenCode correction package, use:

   ```sh
   python3 scripts/recheck_opencode_package.py --package <copied-package>
   ```

5. Compare the new decoded-representation digest, measurement digest, and
   score display with the supplied expected values.
6. Re-run the supplied derived damaged package and verify that the selected
   fact is absent or contradictory under the unchanged observer expectation.

## Receipt fields

The reproducer records their environment, command, package digest, result
digest, damaged-control result, isolation controls, timestamp, and whether
they had access to the original capture. `independent_reproduction: true` is
permitted only when the operator or environment is independent of the
implementation path. A local recomputation remains
`independent_reproduction: false`.

No score, badge, rank, or social-media claim may call a result independently
reproduced until this receipt exists for the corresponding immutable package.
The three historical OpenCode packages are not hand-off candidates because their pinned
decoder source is missing. Their new `evaluation-correction` successors contain a closed
runtime and are the local replay candidates. The redacted semantic derivative is public
inspection material only: it withholds raw SQLite and cannot substitute for the private
copied correction package during independent replay.
