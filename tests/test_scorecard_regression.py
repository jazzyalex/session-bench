"""Focused deterministic regression tests for leaderboard ties and scorecard SVG.

No fixtures, network, or implementation changes. All inputs are inline.
"""

from __future__ import annotations

import re

from session_bench.v1_report import DEFAULT_CATEGORIES, render_scorecard_svg


def _svg_row(
    *,
    row_id="opencode-cli",
    name="OpenCode CLI",
    surface="CLI",
    version="constructed-build-1",
    model="synthetic-model",
    score=83.25,
    category_score=70.0,
):
    categories = [
        {"id": item["id"], "name": item["name"], "score": category_score}
        for item in DEFAULT_CATEGORIES
    ]
    return {
        "id": row_id,
        "name": name,
        "surface": surface,
        "version": version,
        "model": model,
        "score": score,
        "ranking_eligible": True,
        "evidence_complete": True,
        "status": "measured",
        "runs": 3,
        "scheduled_runs": 3,
        "run_range": {"min": 83.0, "max": 83.5},
        "categories": categories,
        "evidence": [{"id": "evidence-1", "locator": "native-locator-1"}],
        "gate_matrix": [
            {
                "id": "gate-1",
                "name": "Gate",
                "state": "measured",
                "detail": "ok",
                "evidence_id": "evidence-1",
            }
        ],
    }


def _svg_report(*, data_status, rows):
    return {
        "edition": "SESSION-BENCH · V1 REPORT",
        "headline": "What did the session keep?",
        "data_status": data_status,
        "configurations": rows,
        "categories": [dict(item) for item in DEFAULT_CATEGORIES],
    }


def test_scorecard_svg_uses_report_data_status_verbatim():
    evidence_status = "EVIDENCE-BOUND PUBLIC RESULT"
    svg = render_scorecard_svg(
        _svg_report(data_status=evidence_status, rows=[_svg_row()])
    )
    assert evidence_status in svg
    assert "CONSTRUCTED CONTROL" not in svg
    # Stable header contract: sub-line at (46,124) echoes data_status.
    assert (
        '<text x="46" y="124" class="svg-sub">'
        "EVIDENCE-BOUND PUBLIC RESULT · incomplete rows remain unranked</text>"
        in svg
    )


def test_scorecard_svg_shows_opencode_83_25_as_83_3_half_up():
    svg = render_scorecard_svg(
        _svg_report(
            data_status="EVIDENCE-BOUND PUBLIC RESULT",
            rows=[_svg_row(name="OpenCode CLI", score=83.25)],
        )
    )
    assert "OpenCode CLI" in svg
    # Round-half-up contract: 83.25 publishes as 83.3 (banker's rounding
    # would give 83.2). Assert the stable score-text coordinate/class.
    assert '<text x="72" y="263" class="svg-score">83.3</text>' in svg
    assert '<text x="72" y="263" class="svg-score">83.2</text>' not in svg


def test_scorecard_svg_keeps_long_identity_out_of_category_label_column():
    long_version = "constructed-build-" + "X" * 120 + "-tail-marker-IDENTITY"
    svg = render_scorecard_svg(
        _svg_report(
            data_status="EVIDENCE-BOUND PUBLIC RESULT",
            rows=[
                _svg_row(
                    name="OpenCode CLI",
                    version=long_version,
                    model="synthetic-model-" + "Y" * 80,
                    score=83.25,
                )
            ],
        )
    )
    category_texts = re.findall(
        r'<text x="420"[^>]*class="svg-category"[^>]*>(.*?)</text>', svg
    )
    assert len(category_texts) == 5
    expected = [
        "Record fidelity",
        "Causality &amp; context",
        "Usage &amp; attribution",
        "Portability &amp; openness",
        "Durability &amp; signal",
    ]
    assert category_texts == expected
    for text in category_texts:
        assert "X" * 10 not in text
        assert "IDENTITY" not in text
        assert "constructed-build" not in text
    # Value/bar columns keep their stable coordinates.
    assert svg.count('class="svg-value"') == 5
    assert svg.count('x="1142"') == 5
    assert svg.count('x="610"') >= 5


def test_scorecard_svg_ranks_at_published_one_decimal_precision():
    svg = render_scorecard_svg(
        _svg_report(
            data_status="UNPUBLISHED LOCAL EVIDENCE",
            rows=[
                _svg_row(row_id="codex-cli", name="Codex CLI", score=86.96302853284081),
                _svg_row(row_id="codex-desktop", name="Codex Desktop", surface="Desktop", score=87.02137602535545),
            ],
        )
    )

    assert svg.count("#1") >= 2
    assert 'class="svg-rank">#2</text>' not in svg
