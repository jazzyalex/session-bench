PRAGMA journal_mode = DELETE;
CREATE TABLE run (
  schema_version TEXT NOT NULL,
  run_id TEXT NOT NULL,
  configuration_id TEXT NOT NULL,
  repetition INTEGER NOT NULL
);
CREATE TABLE metrics (
  position INTEGER PRIMARY KEY,
  id TEXT NOT NULL,
  state TEXT NOT NULL,
  correct INTEGER NOT NULL,
  observed_eligible INTEGER NOT NULL,
  decoded_eligible INTEGER NOT NULL
);
INSERT INTO run VALUES ('session-bench-survival-input-v1', 'constructed-equivalent-1', 'constructed-control', 1);
INSERT INTO metrics VALUES
  (1, 'work.submitted_turns', 'measured', 2, 2, 2),
  (2, 'work.visible_responses', 'measured', 2, 2, 2),
  (3, 'work.actions', 'measured', 4, 4, 4),
  (4, 'work.results', 'measured', 4, 4, 4),
  (5, 'work.changed_files', 'measured', 1, 1, 1),
  (6, 'causal.action_result', 'measured', 4, 4, 4),
  (7, 'causal.turn_response', 'measured', 2, 2, 2),
  (8, 'revision.r1', 'measured', 1, 1, 1),
  (9, 'revision.r2', 'measured', 1, 1, 1),
  (10, 'revision.r1_r2_order', 'measured', 1, 1, 1),
  (11, 'revision.final_after_r2', 'measured', 1, 1, 1),
  (12, 'attribution.model_config', 'measured', 2, 2, 2),
  (13, 'attribution.usage', 'measured', 2, 2, 2),
  (14, 'attribution.token_semantics', 'measured', 2, 2, 2),
  (15, 'attribution.reconciliation', 'measured', 1, 1, 1),
  (16, 'portable.complete_root', 'measured', 1, 1, 1),
  (17, 'portable.companions', 'measured', 1, 1, 1),
  (18, 'portable.isolated_decode', 'measured', 1, 1, 1),
  (19, 'portable.canonical_equality', 'measured', 1, 1, 1);
