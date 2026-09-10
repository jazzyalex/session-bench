"""Minimal evaluator tests: determinism, tie handling, input validation.

Run: python3 -m pytest tests/ -q
"""
import hashlib
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent
REPO = HERE.parent
DATA = REPO / "data"
SCRIPTS = REPO / "scripts"


def run_eval(tmp_path, out_name="out.yml"):
    out = tmp_path / out_name
    r = subprocess.run(
        [sys.executable, str(SCRIPTS / "evaluate.py"),
         "--measurements", str(DATA / "measurements.json"),
         "--checklist", str(DATA / "verdicts.yml"),
         "--out", str(out)],
        capture_output=True, text=True, cwd=REPO)
    return r, out


def test_deterministic(tmp_path):
    r1, o1 = run_eval(tmp_path, "a.yml")
    r2, o2 = run_eval(tmp_path, "b.yml")
    assert r1.returncode == 0, r1.stderr
    assert o1.read_text() == o2.read_text()


def test_matches_checked_in_data(tmp_path):
    r, out = run_eval(tmp_path)
    assert r.returncode == 0, r.stderr
    assert out.read_text() == (DATA / "leaderboard.yml").read_text()


def test_output_names_standalone_source(tmp_path):
    import yaml
    r, out = run_eval(tmp_path)
    assert r.returncode == 0, r.stderr
    board = yaml.safe_load(out.read_text())
    assert board["generated_by"] == "scripts/evaluate.py"
    assert board["source_repository"] == "https://github.com/jazzyalex/session-bench"


def test_equal_fractions_share_rank(tmp_path):
    import yaml
    r, out = run_eval(tmp_path)
    data = yaml.safe_load(out.read_text())
    by_frac = {}
    for a in data["agents"]:
        by_frac.setdefault((a["cleared"], a["scored"]), []).append(a["rank"])
    for ranks in by_frac.values():
        assert len(set(ranks)) == 1


def test_provisional_ranges_match_exhaustive_enumeration(tmp_path):
    from fractions import Fraction
    from itertools import product
    import yaml
    r, out = run_eval(tmp_path)
    data = yaml.safe_load(out.read_text())
    agents = data["agents"]
    prov = [a for a in agents if a.get("not_run", 0) > 0]
    expected = {a["slug"]: [None, None] for a in prov}
    for bits in product((0, 1), repeat=len(prov)):
        fracs = {}
        for a in agents:
            extra = 0
            for b, bit in zip(prov, bits):
                if b["slug"] == a["slug"]:
                    extra = bit * a["not_run"]
            fracs[a["slug"]] = Fraction(a["cleared"] + extra, a["scored"] + a["not_run"])
        for a in prov:
            rank = 1 + sum(1 for s, f in fracs.items()
                           if s != a["slug"] and f > fracs[a["slug"]])
            lo, hi = expected[a["slug"]]
            expected[a["slug"]] = [rank if lo is None else min(lo, rank),
                                   rank if hi is None else max(hi, rank)]
    for a in prov:
        assert [a["rank_best"], a["rank_worst"]] == expected[a["slug"]], a["slug"]


def test_invalid_state_rejected(tmp_path):
    bad = tmp_path / "bad.yml"
    src = (DATA / "verdicts.yml").read_text()
    bad.write_text(src.replace("{state: fail, evidence: \"tokens only, no dollar figure anywhere\"}",
                               "{state: maybe, evidence: \"x\"}", 1))
    r = subprocess.run(
        [sys.executable, str(SCRIPTS / "evaluate.py"),
         "--measurements", str(DATA / "measurements.json"),
         "--checklist", str(bad), "--out", str(tmp_path / "o.yml")],
        capture_output=True, text=True, cwd=REPO)
    assert r.returncode != 0
    assert "invalid state" in r.stderr


def test_computed_gate_override_rejected(tmp_path):
    bad = tmp_path / "bad2.yml"
    src = (DATA / "verdicts.yml").read_text()
    bad.write_text(src.replace("  codex:\n    C1:",
                               "  codex:\n    S1: {state: pass, evidence: \"x\"}\n    C1:", 1))
    r = subprocess.run(
        [sys.executable, str(SCRIPTS / "evaluate.py"),
         "--measurements", str(DATA / "measurements.json"),
         "--checklist", str(bad), "--out", str(tmp_path / "o.yml")],
        capture_output=True, text=True, cwd=REPO)
    assert r.returncode != 0
    assert "may not override" in r.stderr


def test_t2_not_run_makes_t3_not_run(tmp_path):
    import yaml
    bad = tmp_path / "t2nr.yml"
    src = (DATA / "verdicts.yml").read_text()
    assert 'T2: {state: fail, evidence: "no version marker (matrix: not_logged)"}' in src
    bad.write_text(src.replace(
        'T2: {state: fail, evidence: "no version marker (matrix: not_logged)"}',
        'T2: {state: not_run, evidence: "hypothetical: could not inspect"}', 1))
    out = tmp_path / "o.yml"
    r = subprocess.run(
        [sys.executable, str(SCRIPTS / "evaluate.py"),
         "--measurements", str(DATA / "measurements.json"),
         "--checklist", str(bad), "--out", str(out)],
        capture_output=True, text=True, cwd=REPO)
    assert r.returncode == 0, r.stderr
    data = yaml.safe_load(out.read_text())
    cursor = next(a for a in data["agents"] if a["slug"] == "cursor")
    assert cursor["results"]["T2"] == "not_run"
    assert cursor["results"]["T3"] == "not_run"
    assert "unresolved" in cursor["notes"]["T3"]


def test_codex_receipt_query_counts_structured_records_only(tmp_path):
    artifact = tmp_path / "rollout.jsonl"
    rows = [
        {"payload": {"type": "reasoning", "summary": [
            {"type": "summary_text", "text": "stored summary"}
        ]}},
        {"payload": {"type": "reasoning", "summary": []}},
        {"payload": {"type": "sub_agent_activity"}},
        {"payload": {"type": "session_meta", "parent_thread_id": "parent"}},
        {"payload": {"type": "message", "text":
                     "mentions sub_agent_activity and parent_thread_id only"}},
    ]
    artifact.write_text("\n".join(json.dumps(row) for row in rows) + "\n")

    script = REPO / "evidence" / "receipt_codex_c6c7.py"
    result = subprocess.run(
        [sys.executable, str(script), str(artifact)],
        capture_output=True, text=True, cwd=REPO)

    assert result.returncode == 0, result.stderr
    assert hashlib.sha256(artifact.read_bytes()).hexdigest() in result.stdout
    assert "reasoning records 2" in result.stdout
    assert "summary_text 1 (50%)" in result.stdout
    assert "sub_agent_activity records 1" in result.stdout
    assert "parent_thread_id-keyed records 1" in result.stdout


CHECKLIST = DATA / "verdicts.yml"
EVALUATOR = SCRIPTS / "evaluate.py"
MEASUREMENTS = DATA / "measurements.json"


def test_citations_preserve_board(tmp_path):
    import yaml
    checklist = yaml.safe_load(CHECKLIST.read_text())
    r, cited = run_eval(tmp_path)
    assert r.returncode == 0, r.stderr
    with_sources = yaml.safe_load(cited.read_text())
    for agent in with_sources["agents"]:
        expected = checklist["verdicts"][agent["slug"]]["O3"]
        assert agent["sources"]["O3"] == {
            key: expected[key] for key in ("source_url", "observed_at")}
    for verdicts in checklist["verdicts"].values():
        for cell in verdicts.values():
            cell.pop("source_url", None)
            cell.pop("observed_at", None)
    uncited = tmp_path / "uncited.yml"
    uncited.write_text(yaml.safe_dump(checklist))
    out = tmp_path / "plain.yml"
    result = subprocess.run([sys.executable, str(EVALUATOR),
        "--measurements", str(MEASUREMENTS), "--checklist", str(uncited),
        "--out", str(out)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    without_sources = yaml.safe_load(out.read_text())
    for board in (with_sources, without_sources):
        for agent in board["agents"]:
            agent.pop("sources")
    assert with_sources == without_sources


def test_invalid_citations_rejected(tmp_path):
    import yaml
    base = yaml.safe_load(CHECKLIST.read_text())
    for citation in (
        {"source_url": "javascript:alert(1)", "observed_at": "2026-09-09"},
        {"source_url": "https://example.com", "observed_at": "2026-02-30"},
        {"source_url": "https://example.com"},
        {"observed_at": "2026-09-09"},
        {"source_url": "https://example.com", "observed_at": 20260909},
    ):
        cell = base["verdicts"]["pi"]["O3"]
        cell.pop("source_url", None)
        cell.pop("observed_at", None)
        cell.update(citation)
        bad = tmp_path / "bad-citation.yml"
        bad.write_text(yaml.safe_dump(base))
        result = subprocess.run([sys.executable, str(EVALUATOR),
            "--measurements", str(MEASUREMENTS), "--checklist", str(bad),
            "--out", str(tmp_path / "invalid.yml")], capture_output=True, text=True)
        assert result.returncode != 0, citation
        assert "pi O3:" in result.stderr
