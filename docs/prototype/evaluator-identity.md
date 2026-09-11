# Evaluator implementation identity

`implementation_sha256` identifies the files that can affect validation,
decoding, field comparison, or result construction. It does not identify the
entire repository.

Before the atlas work, `implementation_digest()` globbed every Python module and
every JSON schema in `schemas/v1`. This meant an unrelated CLI, controller,
registry, or documentation-product addition could create a new evaluation ID
without changing evaluation behavior. The atlas exposed that overbroad identity
boundary.

The evaluator now declares a reviewed file-level dependency set in
`session_bench.evaluate.EVALUATOR_IMPLEMENTATION_PATHS`. It includes the native
decoder and isolation path, evidence/result validators, live-binding validators,
comparison and locator code, and the schemas they load during evaluation. It
excludes fixture generation, CLI presentation, acquisition controllers,
registries, the atlas, and future campaign-plan files.

This policy change intentionally creates a new implementation digest for new
evaluations. Existing stored results keep their original implementation digest
and evaluation ID; they are historical evaluations and are not rewritten.
Re-evaluating an old capture with the current evaluator produces a new evaluation
identity. A pinned regression makes later changes to the declared dependency set
or its bytes explicit, while unrelated atlas and campaign changes cannot alter
that identity.
