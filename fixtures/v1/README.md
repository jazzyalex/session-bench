# Constructed fixture packs

These four packs are locally generated, redistributable synthetic artifacts under the repository MIT license. They are not vendor-native sessions or observed writer results. JSONL and SQLite encode the same independently specified workload; each has an intact pack and a derived removal control. [index.json](index.json) binds each manifest digest. The negative packs identify the exact corresponding baseline manifest in their provenance.

Generate additional malformed, duplicate, unknown, missing-companion, empty, wrong-status, corrupted, or branch controls with `python3 -m session_bench make-fixture --mutation NAME --out UNUSED_PATH`. See [prototype instructions](../../docs/prototype/README.md).

Do not edit evidence in place. Create a derived pack and new manifest/capture identity. The executable fixture-project files are workload assets; the offline evaluator does not execute them.
