"""Focused checks for the local prototype report renderer.

The payload below is intentionally marked as constructed: it exercises the
renderer only and is never a product result fixture.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path

from session_bench.prototype_report import render_report


def _constructed_report() -> dict:
    categories = [
        {"id": "history", "name": "Work history", "weight": 25},
        {"id": "context", "name": "Context visibility", "weight": 20},
        {"id": "usage", "name": "Usage transparency", "weight": 20},
        {"id": "access", "name": "Access + portability", "weight": 15},
        {"id": "storage", "name": "Storage efficiency", "weight": 20},
    ]
    return {
        "edition": "v1 prototype",
        "generated_at": "2026-09-10T12:00:00Z",
        "headline": "A constructed renderer sample, clearly labelled for local development.",
        "method": "A scripted task was observed independently, then its native history was inspected.",
        "categories": categories,
        "configurations": [
            {
                "id": "alpha",
                "name": "Alpha CLI",
                "surface": "CLI / interactive",
                "version": "0.1.0",
                "model": "model-a",
                "status": "pilot / constructed",
                "score": 81,
                "coverage": 88,
                "runs": 3,
                "categories": [
                    {**category, "score": score, "summary": "Observed in the constructed sample."}
                    for category, score in zip(categories, (92, 84, 78, 80, 70))
                ],
                "facts": [
                    {"label": "Physical bundle", "value": "48,320 bytes"},
                    {"label": "Repeated-run range", "value": "42–49 KB"},
                ],
                "composition": [
                    {"label": "history records", "bytes": 12000},
                    {"label": "attachments", "bytes": 4000},
                ],
                "native_bytes": 20000,
                "examples": [
                    {
                        "label": "Failed test and correction",
                        "observed": "pytest test_target.py -> FAIL\n</script><img src=x onerror=alert(1)>",
                        "recorded": "tool_call: pytest test_target.py & status: failed",
                        "locator": {"path": "session.jsonl", "line": 7},
                    }
                ],
                "limitations": ["Constructed capture; no live vendor qualification."],
                "evidence_path": "evidence/alpha",
            },
            {
                "id": "beta",
                "name": "Beta Desktop",
                "surface": "Desktop / local",
                "version": "unknown-build",
                "model": "model-b",
                "status": "pilot / unavailable",
                "score": None,
                "coverage": 40,
                "runs": 1,
                "categories": [
                    {**category, "score": None, "summary": "Unavailable in this constructed sample."}
                    for category in categories
                ],
                "facts": [],
                "composition": [{"label": "native database", "bytes": 100}],
                "native_bytes": None,
                "examples": [],
                "limitations": ["Authentication was unavailable."],
                "evidence_path": "evidence/beta",
            },
        ],
        "limitations": ["This payload is constructed solely for local renderer development."],
    }


def test_render_writes_four_local_artifacts_and_preserves_report_data(tmp_path: Path):
    report = _constructed_report()
    output = tmp_path / "report"
    output.mkdir()
    (output / "keep-me.txt").write_text("unrelated", encoding="utf-8")

    render_report(report, output)

    assert {path.name for path in output.iterdir()} == {
        "index.html", "scorecard.svg", "composition.svg", "report.json", "keep-me.txt"
    }
    assert json.loads((output / "report.json").read_text(encoding="utf-8")) == report
    html = (output / "index.html").read_text(encoding="utf-8")
    assert "Your agent wrote the code." in html
    assert "What did it record?" in html
    assert 'src="scorecard.svg"' in html
    assert 'src="composition.svg"' in html
    assert "<script src=" not in html
    assert "http://" not in html and "https://" not in html
    assert 'href="./evidence/alpha"' in html
    assert 'href="./evidence/beta"' in html
    assert (output / "keep-me.txt").read_text(encoding="utf-8") == "unrelated"


def test_native_excerpts_and_json_script_escape_html_breakouts(tmp_path: Path):
    output = tmp_path / "report"
    render_report(_constructed_report(), output)
    rendered = (output / "index.html").read_text(encoding="utf-8")

    # The visible excerpt is text, and the machine-readable copy cannot close
    # its application/json script element with an attacker-controlled marker.
    assert "&lt;/script&gt;&lt;img src=x onerror=alert(1)&gt;" in rendered
    assert "\\u003c/script\\u003e\\u003cimg src=x onerror=alert(1)\\u003e" in rendered
    assert "</script><img" not in rendered
    assert rendered.count("</script>") == 2  # JSON payload plus the tiny local hook.
    assert "tool_call: pytest test_target.py &amp; status: failed" in rendered

    scorecard = (output / "scorecard.svg").read_text(encoding="utf-8")
    composition = (output / "composition.svg").read_text(encoding="utf-8")
    ET.fromstring(scorecard)
    ET.fromstring(composition)
    assert "session.jsonl" in rendered
    assert "&lt;img" not in scorecard


def test_missing_scores_are_explicit_and_rows_do_not_invent_a_ranking(tmp_path: Path):
    report = _constructed_report()
    output = tmp_path / "report"
    render_report(report, output)
    html = (output / "index.html").read_text(encoding="utf-8")
    scorecard = (output / "scorecard.svg").read_text(encoding="utf-8")
    composition = (output / "composition.svg").read_text(encoding="utf-8")

    assert html.count("Unscored") >= 6
    assert "pilot / unavailable" in html
    assert "Unknown completeness" in html
    assert "no ranking" in html.lower()
    assert "winner" not in html.lower()
    assert "rank 1" not in html.lower()
    assert "first place" not in html.lower()
    assert html.index("Alpha CLI") < html.index("Beta Desktop")
    assert "Unscored" in scorecard
    assert "Unknown / unclassified" in composition


def test_absolute_evidence_path_is_visible_but_never_linked(tmp_path: Path):
    report = _constructed_report()
    report["configurations"][0]["evidence_path"] = "/private/capture"
    output = tmp_path / "report"
    render_report(report, output)
    html = (output / "index.html").read_text(encoding="utf-8")

    assert "Evidence path unavailable: /private/capture" in html
    assert 'href="/private/capture"' not in html
