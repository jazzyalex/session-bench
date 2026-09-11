# Advanced constructed scenario fixtures

These redistributable fixtures exercise advanced Session-Bench measurement
contracts without making vendor claims.

- `c04-portability-intact` contains one scenario run and attempt represented by
  two physical native JSONL session artifacts and a session-B companion.
- `c04-portability-damaged` changes only the copied session-B continuation token
  and parent-session identity. Its frozen observer and assertions cause C04 to
  report loss rather than preservation.
- `c04-portability-missing-companion` removes the declared session-B companion.
  The capture is invalid evidence; it cannot become an empty or passing result.

Each derived fixture has its own run and capture identity, retains the source
manifest digest, and lists exact changed native locations with before/after
digests. `tests/test_c04_portability.py` regenerates all three packs and compares
their full file inventories and digests.
