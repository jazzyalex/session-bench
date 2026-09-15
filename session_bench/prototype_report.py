"""Render a small, self-contained Session-Bench prototype report.

The report is deliberately a renderer rather than an evaluator.  It accepts the
already-normalized report dictionary produced by the measurement side of the
prototype and keeps the evidence boundary visible in the resulting page.  The
HTML, CSS, JavaScript, and SVG assets are all local so a copied report can be
opened without a build step or a network connection.
"""

from __future__ import annotations

import html
import json
import math
import re
import textwrap
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping, Sequence


HERO = "Your agent wrote the code. What did it record?"

_COLORS = (
    "#ef6c5b",  # coral
    "#2868a6",  # blue
    "#d6a52f",  # ochre
    "#4f896f",  # green
    "#986584",  # plum
    "#7c8f9f",  # slate
)
_PAPER = "#fbf4ea"
_INK = "#172631"
_MUTED = "#5a6870"
_LINE = "#d9cec0"


def _string(value: Any, fallback: str = "Unknown") -> str:
    """Return a useful display string without turning null into a fake value."""

    if value is None:
        return fallback
    if isinstance(value, str):
        return value if value else fallback
    return str(value)


def _html(value: Any, fallback: str = "Unknown") -> str:
    return html.escape(_string(value, fallback), quote=True)


def _svg(value: Any, fallback: str = "Unknown") -> str:
    # html.escape's XML-safe output is suitable for text nodes and attributes.
    return html.escape(_string(value, fallback), quote=True)


def _number(value: Any) -> float | None:
    """Return a finite number, preserving the distinction between null and 0."""

    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        result = float(value)
    else:
        try:
            result = float(str(value).replace(",", "").strip())
        except (TypeError, ValueError):
            return None
    return result if math.isfinite(result) else None


def _score(value: Any) -> float | None:
    value = _number(value)
    if value is None:
        return None
    # Scores are declared normalized to 0..100.  Clamp only for drawing a bar;
    # the source value remains untouched in report.json.
    return max(0.0, min(100.0, value))


def _score_label(value: Any) -> str:
    score = _score(value)
    if score is None:
        return "Unscored"
    return f"{score:.0f}" if score.is_integer() else f"{score:.1f}"


def _coverage_label(value: Any) -> str:
    coverage = _number(value)
    if coverage is None:
        return "Unknown"
    coverage = max(0.0, min(100.0, coverage))
    return f"{coverage:.0f}%" if coverage.is_integer() else f"{coverage:.1f}%"


def _date_label(value: Any) -> str:
    """Keep the masthead scannable while retaining the full timestamp in JSON."""

    text = _string(value, "Date unknown")
    match = re.match(r"(\d{4}-\d{2}-\d{2})", text)
    return match.group(1) if match else text


def _int_bytes(value: Any) -> int | None:
    number = _number(value)
    if number is None or number < 0:
        return None
    return int(number)


def _bytes_label(value: Any) -> str:
    number = _int_bytes(value)
    if number is None:
        return "Unknown"
    return f"{number:,} B"


def _pretty(value: Any, fallback: str = "Unknown / not supplied") -> str:
    if value is None:
        return fallback
    if isinstance(value, str):
        return value if value else fallback
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2)
    except (TypeError, ValueError):
        return str(value)


def _excerpt(value: Any, *, max_chars: int = 1200) -> str:
    text = _pretty(value)
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "\n[… excerpt truncated in this preview; open evidence for the complete record.]"


def _script_json(value: Any) -> str:
    """Encode data for an application/json script without an HTML break-out."""

    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    # A JSON script element still ends at </script>; escape HTML-significant
    # characters while retaining readable Unicode in report.json.
    return (
        encoded.replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def _slug(value: Any, fallback: str = "item") -> str:
    result = re.sub(r"[^a-z0-9]+", "-", _string(value, fallback).lower()).strip("-")
    return result or fallback


def _relative_href(value: Any) -> str | None:
    """Return a safe link rooted at the report directory.

    Evidence captures are copied below ``evidence/`` by the caller.  Absolute
    paths, protocol URLs, and parent traversal are rendered as plain text so a
    report cannot silently point outside its copied bundle.
    """

    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip().replace("\\", "/")
    if raw.startswith(("/", "//")) or "://" in raw:
        return None
    path = PurePosixPath(raw)
    if any(part in ("", "..") for part in path.parts) or ":" in path.parts[0]:
        return None
    normalized = path.as_posix()
    if normalized in ("", ".") or normalized.startswith("../"):
        return None
    return f"./{normalized}"


def _evidence_link(config: Mapping[str, Any], *, css_class: str = "") -> str:
    path = config.get("evidence_path")
    href = _relative_href(path)
    if href is None:
        if path:
            return f'<span class="evidence-missing {css_class}">Evidence path unavailable: {_html(path)}</span>'
        return f'<span class="evidence-missing {css_class}">Evidence path unknown</span>'
    label = _html(path)
    return f'<a class="evidence-link {css_class}" href="{html.escape(href, quote=True)}">Open evidence <span>{label}</span></a>'


def _paragraphs(value: Any, fallback: str) -> str:
    text = _string(value, fallback)
    chunks = [chunk.strip() for chunk in text.split("\n\n") if chunk.strip()]
    if not chunks:
        chunks = [fallback]
    return "".join(f"<p>{_html(chunk)}</p>" for chunk in chunks)


def _iter_categories(report: Mapping[str, Any], config: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Align per-configuration categories to the report's declared order."""

    declared = report.get("categories")
    config_categories = config.get("categories")
    declared = declared if isinstance(declared, list) else []
    config_categories = config_categories if isinstance(config_categories, list) else []
    by_id = {
        item.get("id"): item
        for item in config_categories
        if isinstance(item, Mapping) and item.get("id") is not None
    }
    aligned: list[dict[str, Any]] = []
    for item in declared:
        if not isinstance(item, Mapping):
            continue
        category = dict(item)
        source = by_id.get(item.get("id"))
        if source is not None:
            category.update(source)
        aligned.append(category)
    # Be tolerant of a report assembled without top-level categories while
    # preserving the configuration's declared order.
    if not aligned:
        aligned = [dict(item) for item in config_categories if isinstance(item, Mapping)]
    return aligned


def _category_score(config: Mapping[str, Any], category_id: Any) -> float | None:
    for item in config.get("categories", []) if isinstance(config.get("categories"), list) else []:
        if isinstance(item, Mapping) and item.get("id") == category_id:
            return _score(item.get("score"))
    return None


def _category_summary(config: Mapping[str, Any], category_id: Any) -> str:
    for item in config.get("categories", []) if isinstance(config.get("categories"), list) else []:
        if isinstance(item, Mapping) and item.get("id") == category_id:
            return _string(item.get("summary"), "No summary supplied")
    return "No observation supplied"


def _status_label(value: Any) -> str:
    value = _string(value, "pilot / status unknown")
    return value.replace("_", " ").replace("-", " ")


def _status_class(value: Any) -> str:
    text = _string(value, "status").lower()
    # Keep pilot/unavailable states visually prominent even when a producer
    # adds a second qualifier such as "pilot / constructed".
    for token in ("pilot", "constructed", "unavailable", "unverified", "unknown", "invalid", "incomplete"):
        if token in text:
            return token
    return _slug(value, "status")


def _runs_total(configurations: Sequence[Mapping[str, Any]]) -> int | None:
    values: list[int] = []
    for config in configurations:
        value = config.get("runs")
        if isinstance(value, bool):
            continue
        try:
            number = int(value)
        except (TypeError, ValueError):
            continue
        if number >= 0:
            values.append(number)
    return sum(values) if values else None


def _fact_texts(config: Mapping[str, Any]) -> Iterable[str]:
    for fact in config.get("facts", []) if isinstance(config.get("facts"), list) else []:
        if not isinstance(fact, Mapping):
            continue
        yield " ".join(
            _string(fact.get(key), "")
            for key in ("label", "value", "detail")
            if fact.get(key) not in (None, "")
        )


def _fact_byte_value(config: Mapping[str, Any], keywords: tuple[str, ...]) -> int | None:
    """Find an explicitly labelled byte fact; never estimate physical size."""

    for key in ("physical_bytes", "physical_bundle_bytes", "bundle_bytes", "disk_bytes"):
        if key in config:
            value = _int_bytes(config.get(key))
            if value is not None:
                return value
    for fact in config.get("facts", []) if isinstance(config.get("facts"), list) else []:
        if not isinstance(fact, Mapping):
            continue
        label = _string(fact.get("label"), "").lower()
        if not any(keyword in label for keyword in keywords):
            continue
        for candidate in (fact.get("bytes"), fact.get("value"), fact.get("detail")):
            value = _int_bytes(candidate)
            if value is not None:
                return value
            if isinstance(candidate, str):
                match = re.search(r"(?<![\d.])([\d][\d,]*(?:\.\d+)?)\s*(?:bytes?|b)\b", candidate, re.I)
                if match:
                    try:
                        return int(float(match.group(1).replace(",", "")))
                    except ValueError:
                        pass
    return None


def _repetition_note(configurations: Sequence[Mapping[str, Any]]) -> str:
    candidates: list[str] = []
    for config in configurations:
        for fact in config.get("facts", []) if isinstance(config.get("facts"), list) else []:
            if not isinstance(fact, Mapping):
                continue
            label = _string(fact.get("label"), "").lower()
            if any(word in label for word in ("range", "repeat", "repetition", "sample")):
                value = " ".join(
                    _string(fact.get(key), "")
                    for key in ("value", "detail")
                    if fact.get(key) not in (None, "")
                )
                if value:
                    candidates.append(value)
    if not candidates:
        return "Repeated-run range: unavailable"
    unique = list(dict.fromkeys(candidates))
    return "Repeated-run range: " + " · ".join(unique)


def _composition_data(config: Mapping[str, Any]) -> tuple[list[tuple[str, int]], int | None, int]:
    segments: list[tuple[str, int]] = []
    for item in config.get("composition", []) if isinstance(config.get("composition"), list) else []:
        if not isinstance(item, Mapping):
            continue
        value = _int_bytes(item.get("bytes"))
        if value is not None:
            segments.append((_string(item.get("label"), "Unlabelled"), value))
    native = _int_bytes(config.get("native_bytes"))
    declared = sum(value for _, value in segments)
    unknown = max(native - declared, 0) if native is not None else 0
    return segments, native, unknown


def _composition_state(config: Mapping[str, Any]) -> tuple[str, str]:
    segments, native, unknown = _composition_data(config)
    if native is None:
        return "Unknown completeness", "The captured file-byte total was not supplied."
    declared = sum(value for _, value in segments)
    if declared > native:
        excess = declared - native
        return "Inconsistent composition", f"Declared components exceed the native total by {excess:,} B."
    if unknown:
        return "Partial composition", f"{unknown:,} B remains unknown or unclassified."
    if declared == native:
        return "Byte totals reconcile", "Declared components sum to the captured file-byte total."
    return "No component bytes", "The native total is present, but no component population was supplied."


def _bar_width(value: Any, width: float) -> float:
    score = _score(value)
    return width * score / 100.0 if score is not None else 0.0


def _svg_lines(value: Any, *, max_chars: int = 32) -> list[str]:
    text = _string(value, "Unknown")
    lines = textwrap.wrap(text, width=max_chars, break_long_words=False, break_on_hyphens=False)
    return lines or ["Unknown"]


def _svg_text(value: Any, x: float, y: float, *, class_name: str = "", max_chars: int = 32,
              anchor: str = "start", line_height: int = 14) -> str:
    lines = _svg_lines(value, max_chars=max_chars)
    class_attr = f' class="{html.escape(class_name, quote=True)}"' if class_name else ""
    tspans = []
    for index, line in enumerate(lines):
        dy = 0 if index == 0 else line_height
        tspans.append(f'<tspan x="{x:g}" dy="{dy:g}">{_svg(line)}</tspan>')
    return f'<text x="{x:g}" y="{y:g}" text-anchor="{anchor}"{class_attr}>' + "".join(tspans) + "</text>"


def render_scorecard_svg(report: Mapping[str, Any]) -> str:
    """Return the 1200×675 scorecard master used by the HTML download."""

    configurations = [
        item for item in report.get("configurations", [])
        if isinstance(item, Mapping)
    ]
    categories = [
        item for item in report.get("categories", [])
        if isinstance(item, Mapping)
    ]
    if not categories and configurations:
        categories = _iter_categories(report, configurations[0])
    run_total = _runs_total(configurations)
    run_label = f"{run_total} recorded runs" if run_total is not None else "recorded run count unknown"
    edition = _string(report.get("edition"), "v1 prototype")
    report_url = _relative_href(report.get("report_url")) or "./index.html"
    rows = []
    row_height = 150
    for index, config in enumerate(configurations):
        y = 185 + index * row_height
        config_name = _string(config.get("name"), f"Configuration {index + 1}")
        surface = _string(config.get("surface"), "Surface unknown")
        version = _string(config.get("version"), "Build unknown")
        model = _string(config.get("model"), "Model unknown")
        status = _status_label(config.get("status"))
        runs = _string(config.get("runs"), "runs unknown")
        score_class = " score-unscored" if _score(config.get("score")) is None else ""
        row_id = _slug(config.get("id"), f"configuration-{index + 1}")
        rows.append(
            f'<g id="config-row-{html.escape(row_id, quote=True)}" data-config-id="{html.escape(row_id, quote=True)}">'
            f'<title>{_svg(config_name)} · {_svg(surface)} · {_svg(version)}</title>'
            f'<rect x="44" y="{y - 17}" width="1112" height="132" rx="12" class="row-fill"/>'
            f'<rect x="44" y="{y - 17}" width="7" height="132" rx="3" fill="{_COLORS[index % len(_COLORS)]}"/>'
            f'{_svg_text(config_name, 72, y + 8, class_name="row-name", max_chars=26)}'
            f'{_svg_text(f"{surface} · {version}", 72, y + 42, class_name="row-meta", max_chars=38)}'
            f'{_svg_text(f"{model} · {runs} runs", 72, y + 62, class_name="row-meta", max_chars=40)}'
            f'<rect x="290" y="{y - 2}" width="1" height="74" class="rule"/>'
            f'<text x="314" y="{y + 27}" class="score-number{score_class}">{_svg(_score_label(config.get("score")))}</text>'
            f'<text x="314" y="{y + 48}" class="score-denom">TOTAL / 100 · FULL EVIDENCE</text>'
            f'<text x="314" y="{y + 68}" class="status">{_svg(status)}</text>'
        )
        category_y = y - 5
        for category_index, category in enumerate(categories[:5]):
            category_id = category.get("id")
            category_name = _string(category.get("name"), _string(category_id, "Category"))
            score = _category_score(config, category_id)
            track_width = 530
            bar_y = category_y + category_index * 22
            rows.append(
                f'<text x="350" y="{bar_y + 11}" class="category-label">{_svg(category_name)}</text>'
                f'<rect x="535" y="{bar_y}" width="530" height="10" rx="5" class="bar-track"/>'
            )
            if score is None:
                rows.append(f'<rect x="535" y="{bar_y}" width="530" height="10" rx="5" class="bar-unknown"/>')
            else:
                rows.append(
                    f'<rect x="535" y="{bar_y}" width="{_bar_width(score, track_width):.2f}" height="10" rx="5" '
                    f'fill="{_COLORS[category_index % len(_COLORS)]}"/>'
                )
            rows.append(f'<text x="1090" y="{bar_y + 11}" class="category-score">{_svg(_score_label(score))} pts</text>')
        rows.append("</g>")
    if not rows:
        rows.append('<text x="600" y="350" text-anchor="middle" class="empty">No configurations supplied</text>')
    height = max(675, 185 + max(len(configurations), 1) * row_height)
    category_names = ", ".join(_string(item.get("name"), "Category") for item in categories[:5])
    description = (
        f"{edition}; {len(configurations)} configurations; {run_label}. "
        f"Category bars: {category_names or 'no categories supplied'}. Rows follow collection order and do not imply a ranking."
    )
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="{height}" viewBox="0 0 1200 {height}" role="img" aria-labelledby="scorecard-title scorecard-desc">
  <title id="scorecard-title">What each coding agent records · Session-Bench scorecard</title>
  <desc id="scorecard-desc">{_svg(description)}</desc>
  <defs>
    <pattern id="scorecard-unscored" width="8" height="8" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
      <rect width="8" height="8" fill="#e8dfd4"/><rect width="3" height="8" fill="#c9bcae"/>
    </pattern>
    <style>
      text {{ font-family: "Avenir Next", "Helvetica Neue", sans-serif; fill: {_INK}; }}
      .kicker {{ font-size: 13px; font-weight: 700; letter-spacing: 2px; fill: #2868a6; }}
      .title {{ font-family: "Iowan Old Style", "Palatino Linotype", Georgia, serif; font-size: 31px; font-weight: 700; }}
      .sub {{ font-size: 13px; fill: {_MUTED}; }}
      .row-fill {{ fill: #fffaf3; stroke: {_LINE}; stroke-width: 1; }}
      .rule {{ stroke: {_LINE}; }}
      .row-name {{ font-family: "Iowan Old Style", "Palatino Linotype", Georgia, serif; font-size: 18px; font-weight: 700; }}
      .row-meta {{ font-size: 11px; fill: {_MUTED}; }}
      .score-number {{ font-family: "Iowan Old Style", "Palatino Linotype", Georgia, serif; font-size: 32px; font-weight: 700; }}
      .score-unscored {{ font-size: 17px; letter-spacing: 0; }}
      .score-denom, .status {{ font-size: 9px; letter-spacing: 1.2px; fill: {_MUTED}; }}
      .category-label {{ font-size: 10px; fill: {_MUTED}; }}
      .category-score {{ font-size: 10px; font-weight: 700; }}
      .bar-track {{ fill: #e8dfd4; }}
      .bar-unknown {{ fill: url(#scorecard-unscored); }}
      .empty {{ font-family: "Iowan Old Style", Georgia, serif; font-size: 22px; }}
      .footer {{ font-size: 11px; fill: {_MUTED}; }}
    </style>
  </defs>
  <rect width="1200" height="{height}" fill="{_PAPER}"/>
  <circle cx="1135" cy="54" r="25" fill="#ef6c5b" opacity=".92"/>
  <circle cx="1150" cy="41" r="13" fill="#f1c95e"/>
  <text x="60" y="52" class="kicker">SESSION-BENCH · {_svg(edition.upper())}</text>
  <text x="60" y="96" class="title">What each coding agent records</text>
  <text x="60" y="121" class="sub">Evidenced points / 100 · {_svg(run_label)} · rows follow collection order · no ranking implied</text>
  <text x="1140" y="96" text-anchor="end" class="sub">Report · {_svg(report_url)}</text>
  <text x="350" y="164" class="kicker" style="font-size:10px;letter-spacing:1px">FIVE ANGLES · SUPPLIED SCORES ONLY</text>
  {''.join(rows)}
  <text x="60" y="{height - 20}" class="footer">Full surfaces, builds, sample counts, unknowns, and evidence links are in index.html · report.json</text>
</svg>
'''


def render_composition_svg(report: Mapping[str, Any]) -> str:
    """Return the 1200×675 storage-composition master."""

    configurations = [
        item for item in report.get("configurations", [])
        if isinstance(item, Mapping)
    ]
    run_note = _repetition_note(configurations)
    rows: list[str] = []
    row_height = 156
    for index, config in enumerate(configurations):
        y = 174 + index * row_height
        name = _string(config.get("name"), f"Configuration {index + 1}")
        segments, native, unknown = _composition_data(config)
        physical = _fact_byte_value(config, ("physical", "bundle", "disk", "footprint"))
        state, detail = _composition_state(config)
        declared = sum(value for _, value in segments)
        total = max(native or 0, declared)
        total = total if total > 0 else None
        row_id = _slug(config.get("id"), f"configuration-{index + 1}")
        rows.append(
            f'<g id="composition-row-{html.escape(row_id, quote=True)}" data-config-id="{html.escape(row_id, quote=True)}">'
            f'<title>{_svg(name)} · {_svg(state)}</title>'
            f'<rect x="44" y="{y - 20}" width="1112" height="130" rx="12" class="row-fill"/>'
            f'<rect x="44" y="{y - 20}" width="7" height="130" rx="3" fill="{_COLORS[index % len(_COLORS)]}"/>'
            f'{_svg_text(name, 72, y + 9, class_name="row-name", max_chars=29)}'
            f'{_svg_text(_status_label(config.get("status")), 72, y + 39, class_name="row-meta", max_chars=31)}'
            f'<text x="72" y="{y + 78}" class="state">{_svg(state)}</text>'
            f'{_svg_text(detail, 72, y + 96, class_name="row-meta", max_chars=44)}'
        )
        bar_x, bar_width, bar_h = 360, 485, 24
        if total is None:
            rows.append(f'<rect x="{bar_x}" y="{y - 2}" width="{bar_width}" height="{bar_h}" rx="12" class="bar-unknown"/>')
            rows.append(f'<text x="{bar_x + bar_width + 16}" y="{y + 15}" class="bar-value">Unknown native total</text>')
        else:
            cursor = bar_x
            if segments:
                for segment_index, (label, value) in enumerate(segments):
                    width = bar_width * value / total if total else 0
                    if width <= 0:
                        continue
                    rows.append(
                        f'<rect x="{cursor:.2f}" y="{y - 2}" width="{width:.2f}" height="{bar_h}" '
                        f'fill="{_COLORS[segment_index % len(_COLORS)]}"/>'
                    )
                    cursor += width
            if unknown:
                width = bar_width * unknown / total if total else 0
                rows.append(
                    f'<rect x="{cursor:.2f}" y="{y - 2}" width="{width:.2f}" height="{bar_h}" class="bar-unknown"/>'
                )
            rows.append(f'<rect x="{bar_x}" y="{y - 2}" width="{bar_width}" height="{bar_h}" rx="12" class="bar-outline"/>')
            rows.append(f'<text x="{bar_x + bar_width + 16}" y="{y + 15}" class="bar-value">{_svg(_bytes_label(native))} native</text>')
        rows.append(f'<text x="360" y="{y + 45}" class="bar-caption">CAPTURED FILE BYTES · {_svg(_bytes_label(native))}</text>')
        physical_label = _bytes_label(physical) if physical is not None else "Unknown / not measured"
        rows.append(f'<text x="850" y="{y + 45}" class="bar-caption">PHYSICAL BUNDLE · {_svg(physical_label)}</text>')
        # A compact legend keeps unknown/unclassified bytes explicit even when
        # a segment is too small to label inside the bar.
        legend_y = y + 70
        for segment_index, (label, value) in enumerate(segments[:3]):
            legend_x = 360 + (segment_index % 2) * 230
            legend_row = legend_y + (segment_index // 2) * 17
            rows.append(
                f'<rect x="{legend_x}" y="{legend_row - 9}" width="9" height="9" rx="2" fill="{_COLORS[segment_index % len(_COLORS)]}"/>'
                f'<text x="{legend_x + 16}" y="{legend_row}" class="legend">{_svg(label)} · {_svg(_bytes_label(value))}</text>'
            )
        if unknown or native is None:
            legend_x = 590
            rows.append(
                f'<rect x="{legend_x}" y="{legend_y - 9}" width="9" height="9" rx="2" class="bar-unknown"/>'
                f'<text x="{legend_x + 16}" y="{legend_y}" class="legend">Unknown / unclassified · {_svg(_bytes_label(unknown if native is not None else None))}</text>'
            )
        rows.append("</g>")
    if not rows:
        rows.append('<text x="600" y="350" text-anchor="middle" class="empty">No configurations supplied</text>')
    height = max(675, 174 + max(len(configurations), 1) * row_height)
    description = (
        f"Storage composition for {len(configurations)} configurations. "
        "Bars show declared captured file-byte populations; physical bundle bytes are shown only when explicitly supplied. "
        "Unknown and unclassified bytes remain visible. " + run_note
    )
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="{height}" viewBox="0 0 1200 {height}" role="img" aria-labelledby="composition-title composition-desc">
  <title id="composition-title">Storage composition · Session-Bench</title>
  <desc id="composition-desc">{_svg(description)}</desc>
  <defs>
    <pattern id="composition-unknown" width="8" height="8" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
      <rect width="8" height="8" fill="#e8dfd4"/><rect width="3" height="8" fill="#c9bcae"/>
    </pattern>
    <style>
      text {{ font-family: "Avenir Next", "Helvetica Neue", sans-serif; fill: {_INK}; }}
      .kicker {{ font-size: 13px; font-weight: 700; letter-spacing: 2px; fill: #2868a6; }}
      .title {{ font-family: "Iowan Old Style", "Palatino Linotype", Georgia, serif; font-size: 31px; font-weight: 700; }}
      .sub {{ font-size: 13px; fill: {_MUTED}; }}
      .row-fill {{ fill: #fffaf3; stroke: {_LINE}; stroke-width: 1; }}
      .row-name {{ font-family: "Iowan Old Style", "Palatino Linotype", Georgia, serif; font-size: 18px; font-weight: 700; }}
      .row-meta, .legend {{ font-size: 11px; fill: {_MUTED}; }}
      .state {{ font-size: 10px; font-weight: 700; fill: #2868a6; }}
      .bar-unknown {{ fill: url(#composition-unknown); }}
      .bar-outline {{ fill: none; stroke: #ab9d8d; stroke-width: 1; }}
      .bar-value {{ font-size: 12px; font-weight: 700; }}
      .bar-caption {{ font-size: 9px; letter-spacing: 1px; fill: {_MUTED}; }}
      .empty {{ font-family: "Iowan Old Style", Georgia, serif; font-size: 22px; }}
      .footer {{ font-size: 11px; fill: {_MUTED}; }}
    </style>
  </defs>
  <rect width="1200" height="{height}" fill="{_PAPER}"/>
  <circle cx="1135" cy="54" r="25" fill="#2868a6" opacity=".92"/>
  <circle cx="1150" cy="41" r="13" fill="#ef6c5b"/>
  <text x="60" y="52" class="kicker">SESSION-BENCH · STORAGE TRACE</text>
  <text x="60" y="96" class="title">What fills the record?</text>
  <text x="60" y="121" class="sub">Captured file bytes · physical bundle bytes beside them · unknowns stay on the page</text>
  <text x="1140" y="96" text-anchor="end" class="sub">{_svg(run_note)}</text>
  {''.join(rows)}
  <text x="60" y="{height - 20}" class="footer">Composition labels are declared populations; missing physical totals remain unavailable in this edition.</text>
</svg>
'''


def _render_score_cards(report: Mapping[str, Any], configurations: Sequence[Mapping[str, Any]]) -> str:
    cards: list[str] = []
    for index, config in enumerate(configurations):
        categories = _iter_categories(report, config)
        category_rows: list[str] = []
        for category_index, category in enumerate(categories[:5]):
            category_id = category.get("id")
            score = _category_score(config, category_id)
            width = _bar_width(score, 100)
            category_rows.append(
                f'<div class="category-row">'
                f'<div class="category-heading"><span>{_html(category.get("name"), _string(category_id, "Category"))}</span>'
                f'<span class="category-value">{_html(_score_label(score))} <small>evidenced pts</small></span></div>'
                f'<div class="category-track" aria-hidden="true"><span class="category-fill" style="width:{width:.2f}%"></span></div>'
                f'<p class="category-summary">{_html(_category_summary(config, category_id))}</p>'
                f'</div>'
            )
        if not category_rows:
            category_rows.append('<p class="unknown-copy">Category scores unavailable.</p>')
        status = _status_label(config.get("status"))
        status_class = _status_class(config.get("status"))
        score_class = " score-unscored" if _score(config.get("score")) is None else ""
        cards.append(
            f'<article class="score-card" id="config-{_slug(config.get("id"), f"configuration-{index + 1}")}">'
            f'<div class="card-accent" style="background:{_COLORS[index % len(_COLORS)]}" aria-hidden="true"></div>'
            f'<div class="score-card-header"><div><p class="card-index">CONFIGURATION {index + 1:02d}</p>'
            f'<h3>{_html(config.get("name"), f"Configuration {index + 1}")}</h3>'
            f'<p class="card-meta">{_html(config.get("surface"), "Surface unknown")} · {_html(config.get("version"), "Build unknown")} · {_html(config.get("model"), "Model unknown")}</p></div>'
            f'<span class="status-pill status-{html.escape(status_class, quote=True)}">{_html(status)}</span></div>'
            f'<div class="score-line"><span class="score-number{score_class}">{_html(_score_label(config.get("score")))}</span><span class="score-out-of">/ 100 total · full evidence only</span></div>'
            f'<dl class="card-facts"><div><dt>Coverage</dt><dd>{_html(_coverage_label(config.get("coverage")))}</dd></div>'
            f'<div><dt>Runs</dt><dd>{_html(config.get("runs"), "Unknown")}</dd></div></dl>'
            f'{_render_facts(config)}'
            f'<div class="category-list">{"".join(category_rows)}</div>'
            f'{_evidence_link(config)}'
            f'</article>'
        )
    return "".join(cards) or '<p class="empty-state">No configurations supplied.</p>'


def _render_score_table(report: Mapping[str, Any], configurations: Sequence[Mapping[str, Any]]) -> str:
    categories = [item for item in report.get("categories", []) if isinstance(item, Mapping)]
    headings = "".join(f"<th scope=\"col\">{_html(item.get('name'), _string(item.get('id'), 'Category'))}</th>" for item in categories[:5])
    rows: list[str] = []
    for index, config in enumerate(configurations):
        cells = "".join(
            f'<td data-label="{_html(category.get("name"), "Category")}">{_html(_score_label(_category_score(config, category.get("id"))))}</td>'
            for category in categories[:5]
        )
        rows.append(
            f'<tr><th scope="row">{_html(config.get("name"), f"Configuration {index + 1}")}</th>'
            f'<td>{_html(config.get("surface"), "Unknown surface")}<small>{_html(config.get("version"), "Unknown build")}</small></td>'
            f'<td>{_html(_status_label(config.get("status")))}</td>'
            f'<td class="table-score">{_html(_score_label(config.get("score")))}</td>'
            f'<td>{_html(_coverage_label(config.get("coverage")))}</td>'
            f'<td>{_html(config.get("runs"), "Unknown")}</td>{cells}</tr>'
        )
    return (
        '<div class="table-scroll"><table class="score-table"><caption>Text comparison of supplied scores. Category values are evidenced points against a fixed denominator; '
        'Missing scores remain Unscored; rows are not ranked.</caption><thead><tr>'
        '<th scope="col">Configuration</th><th scope="col">Surface / build</th><th scope="col">State</th>'
        '<th scope="col">Total</th><th scope="col">Coverage</th><th scope="col">Runs</th>'
        f'{headings}</tr></thead><tbody>{"".join(rows)}</tbody></table></div>'
    )


def _render_composition_panels(configurations: Sequence[Mapping[str, Any]]) -> str:
    panels: list[str] = []
    for index, config in enumerate(configurations):
        segments, native, unknown = _composition_data(config)
        state, detail = _composition_state(config)
        declared = sum(value for _, value in segments)
        total = max(native or 0, declared)
        total = total if total > 0 else None
        segment_html: list[str] = []
        if total is not None and segments:
            for segment_index, (label, value) in enumerate(segments):
                width = max(0, min(100, 100 * value / total))
                segment_html.append(
                    f'<span class="composition-segment" style="width:{width:.2f}%;background:{_COLORS[segment_index % len(_COLORS)]}" '
                    f'title="{_html(label)} · {_html(_bytes_label(value))}"></span>'
                )
        if unknown or native is None:
            unknown_width = (100 * unknown / total) if total and native is not None else 100
            segment_html.append(
                f'<span class="composition-segment composition-unknown" style="width:{max(0, min(100, unknown_width)):.2f}%" '
                f'title="Unknown / unclassified · {_html(_bytes_label(unknown if native is not None else None))}"></span>'
            )
        if not segment_html:
            segment_html.append('<span class="composition-segment composition-unknown" style="width:100%" title="Unknown composition"></span>')
        legend = "".join(
            f'<li><span class="legend-swatch" style="background:{_COLORS[i % len(_COLORS)]}" aria-hidden="true"></span>{_html(label)} <strong>{_html(_bytes_label(value))}</strong></li>'
            for i, (label, value) in enumerate(segments)
        )
        if unknown or native is None:
            legend += f'<li><span class="legend-swatch legend-unknown" aria-hidden="true"></span>Unknown / unclassified <strong>{_html(_bytes_label(unknown if native is not None else None))}</strong></li>'
        physical = _fact_byte_value(config, ("physical", "bundle", "disk", "footprint"))
        panels.append(
            f'<article class="composition-card"><div class="composition-heading"><div><p class="card-index">CONFIGURATION {index + 1:02d}</p>'
            f'<h3>{_html(config.get("name"), f"Configuration {index + 1}")}</h3></div><span class="state-label">{_html(state)}</span></div>'
            f'<p class="composition-detail">{_html(detail)}</p>'
            f'<div class="composition-bar" role="img" aria-label="Captured file bytes: {_html(_bytes_label(native))}; composition state: {_html(state)}">{"".join(segment_html)}</div>'
            f'<div class="composition-meta"><span>Captured files <strong>{_html(_bytes_label(native))}</strong></span><span>Physical bundle <strong>{_html(_bytes_label(physical) if physical is not None else "Unknown")}</strong></span></div>'
            f'<ul class="composition-legend">{legend or "<li>Composition population unknown</li>"}</ul></article>'
        )
    return "".join(panels) or '<p class="empty-state">No composition records supplied.</p>'


def _render_examples(configurations: Sequence[Mapping[str, Any]]) -> str:
    def render_example(example: Mapping[str, Any]) -> str:
        label = _string(example.get("label"), "Unnamed observation")
        observed = _excerpt(example.get("observed"))
        recorded = _excerpt(example.get("recorded"))
        locator = _pretty(example.get("locator"), "Native locator unknown")
        return (
            f'<article class="example"><h4>{_html(label)}</h4><div class="excerpt-grid">'
            f'<section><p class="excerpt-label"><span class="dot dot-coral" aria-hidden="true"></span>Observed independently</p><pre>{_html(observed)}</pre></section>'
            f'<section><p class="excerpt-label"><span class="dot dot-blue" aria-hidden="true"></span>Recorded in native history</p><pre>{_html(recorded)}</pre></section></div>'
            f'<p class="locator"><span>Exact native locator</span><code>{_html(locator)}</code></p></article>'
        )

    cards: list[str] = []
    for index, config in enumerate(configurations):
        examples = [item for item in config.get("examples", []) if isinstance(item, Mapping)] \
            if isinstance(config.get("examples"), list) else []
        if examples:
            # Prefer a W3/failure excerpt for the first read; other examples
            # remain available in a native collapsed details element.
            def preference(item: Mapping[str, Any]) -> tuple[int, int]:
                label = _string(item.get("label"), "").lower()
                if "w3" in label or "failure" in label or "failed" in label:
                    return (0, 0)
                if "baseline" in label or "helper" in label:
                    return (1, 0)
                return (2, 0)

            selected_index = min(range(len(examples)), key=lambda item: preference(examples[item]))
            selected = examples[selected_index]
            remaining = [item for item_index, item in enumerate(examples) if item_index != selected_index]
            example_html = [render_example(selected)]
            if remaining:
                extra_html = "".join(render_example(item) for item in remaining)
                example_html.append(
                    f'<details class="more-excerpts"><summary>Show {len(remaining)} more native excerpts</summary>{extra_html}</details>'
                )
        else:
            example_html = ['<p class="unknown-copy">Native excerpts unavailable for this configuration.</p>']
        cards.append(
            f'<article class="record-card"><div class="record-header"><div><p class="card-index">CONFIGURATION {index + 1:02d}</p>'
            f'<h3>{_html(config.get("name"), f"Configuration {index + 1}")}</h3></div>{_evidence_link(config, css_class="record-evidence")}</div>'
            f'{"".join(example_html)}</article>'
        )
    return "".join(cards) or '<p class="empty-state">No native excerpts supplied.</p>'


def _hero_fact_chips(configurations: Sequence[Mapping[str, Any]]) -> str:
    known_native = [_int_bytes(config.get("native_bytes")) for config in configurations]
    known_native = [value for value in known_native if value is not None]
    native_label = f"{sum(known_native):,} known native bytes" if known_native else "Native bytes unknown"
    excerpt_count = sum(
        len(config.get("examples", []))
        for config in configurations
        if isinstance(config.get("examples"), list)
    )
    excerpt_label = f"{excerpt_count} native excerpts" if excerpt_count else "Native excerpts unknown"
    usage_known = any(
        _category_score(config, category.get("id")) is not None
        for config in configurations
        for category in config.get("categories", [])
        if isinstance(category, Mapping) and "usage" in _string(category.get("id"), "").lower()
    )
    usage_label = "Usage evidence present" if usage_known else "Usage evidence unknown"
    return "".join(f'<span>{_html(label)}</span>' for label in (native_label, excerpt_label, usage_label))


def _render_facts(config: Mapping[str, Any]) -> str:
    facts = [item for item in config.get("facts", []) if isinstance(item, Mapping)] \
        if isinstance(config.get("facts"), list) else []
    if not facts:
        return ""
    rows = []
    for fact in facts:
        label = _string(fact.get("label"), "Recorded fact")
        value = _pretty(fact.get("value"), "Unknown")
        detail = fact.get("detail")
        detail_html = f'<small>{_html(detail)}</small>' if detail not in (None, "") else ""
        rows.append(f'<div><dt>{_html(label)}</dt><dd>{_html(value)}{detail_html}</dd></div>')
    return (
        f'<details class="facts-details"><summary>Show {len(rows)} recorded facts</summary>'
        f'<dl class="fact-list">{"".join(rows)}</dl></details>'
    )


def _render_unknowns(report: Mapping[str, Any], configurations: Sequence[Mapping[str, Any]]) -> str:
    items: list[str] = []
    for limitation in report.get("limitations", []) if isinstance(report.get("limitations"), list) else []:
        items.append(f'<li><span class="unknown-mark" aria-hidden="true">?</span>{_html(limitation)}</li>')
    for config in configurations:
        name = _string(config.get("name"), "Configuration")
        limitations = config.get("limitations") if isinstance(config.get("limitations"), list) else []
        for limitation in limitations:
            items.append(f'<li><span class="unknown-mark" aria-hidden="true">?</span><strong>{_html(name)}:</strong> {_html(limitation)}</li>')
        if _score(config.get("score")) is None:
            items.append(f'<li><span class="unknown-mark" aria-hidden="true">?</span><strong>{_html(name)}:</strong> total score was not supplied</li>')
    if not items:
        items.append('<li><span class="unknown-mark" aria-hidden="true">?</span>No additional limitations supplied; inspect the evidence paths for the recorded boundary.</li>')
    return "".join(items)


def _render_html(report: Mapping[str, Any], scorecard_svg: str, composition_svg: str) -> str:
    configurations = [item for item in report.get("configurations", []) if isinstance(item, Mapping)]
    category_count = len([item for item in report.get("categories", []) if isinstance(item, Mapping)])
    if not category_count and configurations:
        category_count = len(_iter_categories(report, configurations[0]))
    run_total = _runs_total(configurations)
    run_text = f"{run_total} recorded runs" if run_total is not None else "recorded run count unknown"
    edition = _string(report.get("edition"), "v1 prototype")
    headline = _string(report.get("headline"), "A bright, practical view of what survives the session.")
    generated = report.get("generated_at")
    generated_text = _string(generated, "Generation time unknown")
    hero_deck = (
        "One small coding task, five ways to read the saved record."
        if headline.strip().lower() == HERO.lower()
        else headline
    )
    hero_deck = _string(report.get("deck"), hero_deck)
    pilot_text = "Category measurements are shown; overall totals stay withheld from ranking until evidence is complete."
    hero_facts = (
        f'<span>{len(configurations)} configurations</span>'
        f'<span>{category_count or "Unknown"} category angles</span>'
        f'<span>{_html(run_text)}</span>'
        f'{_hero_fact_chips(configurations)}'
    )
    report_json = _script_json(report)
    # The SVG strings are written as downloads.  The HTML uses local <img>
    # references so screen readers also have the surrounding text table.
    scorecard_alt = f"Session-Bench scorecard for {len(configurations)} configurations; scores and five category bars in collection order."
    composition_alt = f"Session-Bench storage composition for {len(configurations)} configurations; captured file bytes, physical bundle values, and unknown segments."
    return f'''<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="color-scheme" content="light">
  <title>{_html(HERO)} · Session-Bench</title>
  <style>
    :root {{
      --paper: {_PAPER}; --paper-deep: #f2e8db; --ink: {_INK}; --muted: {_MUTED};
      --coral: #ef6c5b; --blue: #2868a6; --ochre: #d6a52f; --line: {_LINE};
      --shadow: 0 18px 48px rgba(42, 34, 24, .09); --radius: 18px;
    }}
    *, *::before, *::after {{ box-sizing: border-box; }}
    html {{ scroll-behavior: smooth; background: var(--paper); }}
    body {{ margin: 0; color: var(--ink); background:
      radial-gradient(circle at 88% 3%, rgba(239,108,91,.16), transparent 22rem),
      radial-gradient(circle at 8% 22%, rgba(40,104,166,.08), transparent 20rem), var(--paper);
      font-family: "Avenir Next", "Helvetica Neue", "Trebuchet MS", sans-serif; line-height: 1.5; }}
    a {{ color: var(--blue); }}
    a:focus-visible, summary:focus-visible, button:focus-visible {{ outline: 3px solid var(--coral); outline-offset: 4px; }}
    h1, h2, h3, h4, .serif {{ font-family: "Iowan Old Style", "Palatino Linotype", "Book Antiqua", Georgia, serif; }}
    h1, h2, h3, h4, p {{ margin-top: 0; }}
    .skip-link {{ position: absolute; left: 1rem; top: .75rem; transform: translateY(-180%); background: var(--ink); color: white; padding: .65rem 1rem; border-radius: 99px; z-index: 10; }}
    .skip-link:focus {{ transform: translateY(0); }}
    .shell {{ max-width: 1280px; padding: 0 42px; margin: 0 auto; }}
    .masthead {{ display:flex; justify-content:space-between; align-items:center; padding: 28px 0 24px; border-bottom: 1px solid var(--line); font-size: .76rem; letter-spacing: .11em; text-transform: uppercase; }}
    .brand {{ display:flex; align-items:center; gap: .7rem; font-weight: 800; }}
    .brand-mark {{ display:grid; place-items:center; width: 28px; height: 28px; border-radius: 8px; background: var(--ink); color: var(--paper); font-family: Georgia, serif; font-size: .77rem; letter-spacing: -.05em; }}
    .mast-meta {{ color: var(--muted); text-align:right; }}
    .hero {{ display:grid; grid-template-columns: minmax(0, 1fr) minmax(220px, .32fr); gap: 6vw; padding: 34px 0 28px; align-items:end; }}
    .eyebrow, .card-index, .section-kicker, .excerpt-label, .locator span {{ font-size: .7rem; letter-spacing: .13em; text-transform: uppercase; font-weight: 800; }}
    .eyebrow {{ color: var(--blue); margin-bottom: 1rem; }}
    .eyebrow .slash {{ color: var(--coral); margin: 0 .35rem; }}
    h1 {{ font-size: clamp(2.5rem, 4.7vw, 3.8rem); line-height: .93; letter-spacing: -.055em; max-width: 950px; margin-bottom: 1.35rem; }}
    h1 em {{ color: var(--coral); font-style: normal; }}
    .hero-deck {{ font-family: Georgia, serif; font-size: clamp(1.15rem, 2vw, 1.55rem); line-height: 1.28; max-width: 720px; margin-bottom: 2rem; }}
    .hero-facts {{ display:flex; gap: .6rem; flex-wrap:wrap; color: var(--muted); font-size: .83rem; }}
    .hero-facts span {{ border: 1px solid var(--line); padding: .48rem .7rem; border-radius: 99px; background: rgba(255,255,255,.35); }}
    .hero-note {{ padding: 16px 18px 15px; max-width: 320px; border-left: 4px solid var(--coral); background: rgba(255,250,243,.72); color: var(--ink); border-radius: 0 12px 12px 0; }}
    .hero-note .note-label {{ color: var(--blue); font-size: .64rem; letter-spacing: .13em; font-weight:800; text-transform:uppercase; margin-bottom:.55rem; }}
    .hero-note p {{ font-family: Georgia, serif; font-size:1rem; line-height:1.25; margin-bottom:0; }}
    .pilot-strip {{ display:flex; gap: .85rem; align-items: baseline; padding: 12px 15px; margin: 0 0 36px; border: 1px solid rgba(239,108,91,.32); border-left: 5px solid var(--coral); border-radius: 0 10px 10px 0; background: rgba(239,108,91,.08); color: var(--ink); }}
    .pilot-strip strong {{ text-transform:uppercase; letter-spacing:.1em; font-size:.69rem; color: var(--coral); white-space:nowrap; }}
    .pilot-strip span {{ color: var(--muted); font-size:.9rem; }}
    .section {{ padding: 0 0 100px; }}
    .section-heading {{ display:grid; grid-template-columns: .33fr 1fr; gap: 2rem; align-items:baseline; margin-bottom: 28px; }}
    .section-kicker {{ color: var(--coral); margin: 0; }}
    h2 {{ font-size: clamp(2.25rem, 5vw, 4.3rem); line-height: .98; letter-spacing:-.045em; margin-bottom: .75rem; }}
    .section-heading .lede {{ max-width: 640px; color: var(--muted); margin-bottom:0; }}
    .score-grid {{ display:grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 18px; align-items:stretch; }}
    .score-card, .composition-card, .record-card {{ position:relative; overflow:hidden; background: rgba(255,250,243,.82); border:1px solid var(--line); border-radius:var(--radius); box-shadow: var(--shadow); }}
    .score-card {{ padding: 27px 25px 23px; }}
    .card-accent {{ position:absolute; top:0; left:0; right:0; height:5px; }}
    .score-card-header, .composition-heading, .record-header {{ display:flex; gap:12px; align-items:flex-start; justify-content:space-between; }}
    .card-index {{ color:var(--muted); margin:0 0 .45rem; font-size:.62rem; }}
    h3 {{ font-size:1.55rem; line-height:1.05; margin-bottom:.58rem; letter-spacing:-.03em; }}
    .card-meta {{ color:var(--muted); font-size:.77rem; line-height:1.35; margin-bottom:1.35rem; }}
    .status-pill {{ flex:0 0 auto; border:1px solid currentColor; border-radius:99px; padding:.29rem .52rem; color:var(--blue); font-size:.62rem; line-height:1.1; text-transform:uppercase; letter-spacing:.08em; font-weight:800; max-width: 112px; text-align:center; }}
    .status-pilot, .status-constructed, .status-prototype {{ color:var(--coral); background:rgba(239,108,91,.1); }}
    .status-unavailable, .status-unknown, .status-unverified {{ color:#8a6732; background:rgba(214,165,47,.14); }}
    .score-line {{ display:flex; align-items:baseline; gap:.35rem; margin: 8px 0 10px; }}
    .score-number {{ font-family: Georgia, serif; font-size: 3.5rem; letter-spacing:-.07em; line-height:1; }}
    .score-number.score-unscored {{ font-size: 1.55rem; letter-spacing:-.02em; line-height:1.25; }}
    .score-out-of {{ color:var(--muted); font-size:.78rem; }}
    .card-facts {{ display:flex; gap: 16px; border-bottom:1px solid var(--line); padding-bottom:15px; margin:0 0 18px; }}
    .card-facts div {{ display:flex; gap:.35rem; align-items:baseline; }}
    .card-facts dt {{ color:var(--muted); font-size:.68rem; text-transform:uppercase; letter-spacing:.08em; }}
    .card-facts dd {{ margin:0; font-family:Georgia,serif; font-size:1.05rem; }}
    .facts-details {{ margin:0 0 17px; padding:0 0 12px; border-bottom:1px solid var(--line); }}
    .facts-details summary {{ cursor:pointer; color:var(--blue); font-size:.7rem; font-weight:800; }}
    .fact-list {{ display:grid; gap:8px; margin:12px 0 0; }}
    .fact-list div {{ display:grid; grid-template-columns:minmax(0,.8fr) minmax(0,1.2fr); gap:10px; }}
    .fact-list dt {{ color:var(--muted); font-size:.64rem; text-transform:uppercase; letter-spacing:.07em; }}
    .fact-list dd {{ min-width:0; margin:0; overflow-wrap:anywhere; font-size:.72rem; }}
    .fact-list small {{ display:block; color:var(--muted); margin-top:2px; font-size:.65rem; }}
    .category-row {{ margin-bottom: 12px; }}
    .category-heading {{ display:flex; justify-content:space-between; gap:10px; font-size:.75rem; }}
    .category-value {{ font-weight:800; color:var(--blue); }}
    .category-value small {{ color:var(--muted); font-size:.58rem; font-weight:700; letter-spacing:.04em; text-transform:uppercase; }}
    .category-track {{ height:6px; background:var(--paper-deep); border-radius:99px; overflow:hidden; margin:5px 0 4px; }}
    .category-fill {{ display:block; height:100%; min-width:0; background:linear-gradient(90deg,var(--blue),#5a8db9); border-radius:99px; }}
    .category-summary {{ color:var(--muted); font-size:.7rem; line-height:1.3; margin-bottom:0; }}
    .evidence-link {{ display:inline-flex; gap:.5rem; align-items:baseline; margin-top:12px; font-size:.76rem; font-weight:700; text-decoration:none; }}
    .evidence-link span {{ color:var(--muted); font-weight:400; overflow-wrap:anywhere; }}
    .evidence-missing {{ display:block; margin-top:12px; color:#8a6732; font-size:.76rem; }}
    .table-scroll {{ overflow-x:auto; margin-top:22px; }}
    .score-table {{ width:100%; border-collapse:collapse; min-width: 840px; font-size:.78rem; }}
    .score-table caption {{ text-align:left; padding:0 0 10px; color:var(--muted); font-size:.75rem; }}
    .score-table th, .score-table td {{ padding:12px 11px; text-align:left; border-bottom:1px solid var(--line); vertical-align:top; }}
    .score-table thead th {{ color:var(--muted); font-size:.63rem; letter-spacing:.09em; text-transform:uppercase; }}
    .score-table tbody th {{ font-family:Georgia,serif; font-size:1rem; }}
    .score-table td small {{ display:block; color:var(--muted); margin-top:2px; }}
    .table-score {{ font-family:Georgia,serif; font-size:1.25rem; }}
    .visual-grid {{ display:grid; grid-template-columns: 1fr 1fr; gap: 18px; }}
    .visual-card {{ padding: 13px; background:#fffaf3; border:1px solid var(--line); border-radius:var(--radius); }}
    .visual-card img {{ display:block; width:100%; height:auto; border-radius:11px; border:1px solid var(--line); background:var(--paper); }}
    .download-row {{ display:flex; gap:12px; flex-wrap:wrap; margin: 14px 4px 0; font-size:.78rem; }}
    .download-row a {{ font-weight:700; }}
    .composition-grid, .record-grid {{ display:grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap:18px; }}
    .composition-card {{ padding:24px 22px; }}
    .state-label {{ color:var(--blue); font-size:.68rem; text-transform:uppercase; letter-spacing:.08em; font-weight:800; text-align:right; }}
    .composition-detail {{ color:var(--muted); font-size:.77rem; line-height:1.35; min-height:2.15em; }}
    .composition-bar {{ display:flex; overflow:hidden; height:24px; border-radius:99px; background:var(--paper-deep); margin:17px 0 9px; }}
    .composition-segment {{ height:100%; min-width:1px; }}
    .composition-unknown, .legend-unknown {{ background:repeating-linear-gradient(135deg,#d5c9bb 0,#d5c9bb 3px,#eee5dc 3px,#eee5dc 7px) !important; }}
    .composition-meta {{ display:flex; justify-content:space-between; gap:10px; color:var(--muted); font-size:.68rem; text-transform:uppercase; letter-spacing:.06em; }}
    .composition-meta strong {{ display:block; color:var(--ink); font-family:Georgia,serif; font-size:.96rem; letter-spacing:0; text-transform:none; }}
    .composition-legend {{ list-style:none; padding:13px 0 0; margin:0; border-top:1px solid var(--line); font-size:.72rem; color:var(--muted); }}
    .composition-legend li {{ display:flex; align-items:center; gap:7px; margin-top:5px; }}
    .composition-legend strong {{ color:var(--ink); margin-left:auto; }}
    .legend-swatch {{ width:9px; height:9px; border-radius:2px; flex:0 0 auto; }}
    .record-card {{ padding:24px 22px; }}
    .record-header {{ border-bottom:1px solid var(--line); padding-bottom:17px; margin-bottom:17px; }}
    .record-evidence {{ margin-top:0; text-align:right; display:block; }}
    .more-excerpts {{ margin-top:16px; border-top:1px solid var(--line); padding-top:12px; }}
    .more-excerpts summary {{ cursor:pointer; color:var(--blue); font-size:.74rem; font-weight:800; }}
    .more-excerpts .example {{ margin-top:18px; }}
    .example {{ padding: 0 0 18px; margin-bottom:18px; border-bottom:1px solid var(--line); }}
    .example:last-child {{ padding-bottom:0; margin-bottom:0; border-bottom:0; }}
    h4 {{ font-size:1.22rem; line-height:1.08; margin-bottom:15px; }}
    .excerpt-grid {{ display:grid; grid-template-columns:1fr 1fr; gap:10px; }}
    .excerpt-grid section {{ min-width:0; }}
    .excerpt-label {{ display:flex; align-items:center; gap:6px; color:var(--muted); font-size:.58rem; letter-spacing:.08em; margin-bottom:7px; }}
    .dot {{ width:7px; height:7px; border-radius:50%; flex:0 0 auto; }}
    .dot-coral {{ background:var(--coral); }} .dot-blue {{ background:var(--blue); }}
    pre {{ min-height: 112px; overflow:auto; margin:0; padding:12px; color:#f9f1e7; background:#22313a; border-radius:10px; font: .7rem/1.45 "SFMono-Regular", Consolas, monospace; white-space:pre-wrap; overflow-wrap:anywhere; }}
    .locator {{ margin:10px 0 0; color:var(--muted); font-size:.7rem; }}
    .locator span {{ display:block; font-size:.58rem; margin-bottom:4px; }}
    code {{ display:block; padding:7px 9px; border-radius:7px; background:var(--paper-deep); overflow-wrap:anywhere; font:.66rem/1.4 "SFMono-Regular", Consolas, monospace; }}
    .unknown-copy, .empty-state {{ color:var(--muted); font-family:Georgia,serif; font-style:italic; }}
    .methodology {{ border-top:1px solid var(--ink); border-bottom:1px solid var(--ink); }}
    .methodology summary {{ display:flex; justify-content:space-between; align-items:baseline; gap:18px; cursor:pointer; list-style:none; padding:23px 0; font-family:Georgia,serif; font-size:1.45rem; }}
    .methodology summary::-webkit-details-marker {{ display:none; }}
    .methodology summary::after {{ content:"+"; color:var(--coral); font-family:"Avenir Next",sans-serif; font-size:1.7rem; }}
    .methodology[open] summary::after {{ content:"−"; }}
    .methodology-body {{ display:grid; grid-template-columns: 1.1fr .9fr; gap:7vw; padding: 0 0 30px; }}
    .method-copy {{ color:var(--muted); max-width:650px; }}
    .limitations-details {{ border-top:1px solid var(--ink); border-bottom:1px solid var(--ink); }}
    .limitations-details summary {{ display:flex; justify-content:space-between; align-items:baseline; gap:18px; cursor:pointer; list-style:none; padding:18px 0; font-family:Georgia,serif; font-size:1.22rem; }}
    .limitations-details summary::-webkit-details-marker {{ display:none; }}
    .limitations-details summary::after {{ content:"+"; color:var(--coral); font-size:1.5rem; }}
    .limitations-details[open] summary::after {{ content:"−"; }}
    .limitations-body {{ padding:0 0 24px; }}
    .weights {{ display:flex; flex-wrap:wrap; gap:8px; align-content:start; }}
    .weight-chip {{ padding:10px 12px; border:1px solid var(--line); border-radius:11px; background:#fffaf3; font-size:.72rem; }}
    .weight-chip strong {{ display:block; color:var(--blue); font-family:Georgia,serif; font-size:1.18rem; }}
    .unknown-list {{ display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:9px 24px; list-style:none; padding:0; margin:0; }}
    .unknown-list li {{ display:flex; gap:9px; color:var(--muted); font-size:.82rem; }}
    .unknown-list strong {{ color:var(--ink); }}
    .unknown-mark {{ display:grid; place-items:center; flex:0 0 auto; width:19px; height:19px; border-radius:50%; background:rgba(214,165,47,.18); color:#8a6732; font-weight:800; }}
    .footer {{ display:flex; justify-content:space-between; gap:1rem; align-items:baseline; padding:34px 0 48px; color:var(--muted); font-size:.72rem; border-top:1px solid var(--line); }}
    .footer-links {{ display:flex; gap:14px; flex-wrap:wrap; }}
    @media (max-width: 980px) {{ .score-grid, .composition-grid, .record-grid {{ grid-template-columns:1fr 1fr; }} .score-card:last-child, .composition-card:last-child, .record-card:last-child {{ grid-column:1 / -1; }} .hero {{ gap:3rem; }} }}
    @media (max-width: 720px) {{ .shell {{ padding:0 20px; }} .masthead {{ align-items:flex-start; gap:18px; }} .mast-meta {{ max-width:130px; }} .hero {{ display:block; padding:42px 0 38px; }} .hero-note {{ max-width:370px; margin:24px 0 0 auto; }} .pilot-strip {{ display:block; margin-bottom:58px; }} .pilot-strip strong {{ display:block; margin-bottom:4px; }} .section-heading, .methodology-body {{ display:block; }} .section-kicker {{ margin-bottom:13px; }} .section-heading .lede {{ margin-bottom:24px; }} .score-grid, .composition-grid, .record-grid, .visual-grid {{ grid-template-columns:1fr; }} .score-card:last-child, .composition-card:last-child, .record-card:last-child {{ grid-column:auto; }} .excerpt-grid {{ grid-template-columns:1fr; }} .unknown-list {{ grid-template-columns:1fr; }} .footer {{ display:block; }} .footer-links {{ margin-top:15px; }} }}
    @media (prefers-reduced-motion: reduce) {{ html {{ scroll-behavior:auto; }} * {{ animation-duration:.001ms !important; transition-duration:.001ms !important; }} }}
  </style>
</head>
<body>
  <a class="skip-link" href="#main">Skip to report</a>
  <div class="shell">
    <header class="masthead"><div class="brand"><span class="brand-mark" aria-hidden="true">SB</span><span>Session-Bench</span></div><div class="mast-meta">{_html(edition)}<br><time datetime="{_html(generated_text)}">{_html(_date_label(generated))}</time></div></header>
    <main id="main">
      <section class="hero" aria-labelledby="report-title">
        <div><p class="eyebrow">A record-quality field note <span class="slash">/</span> {_html(edition)}</p><h1 id="report-title">Your agent wrote the code.<br><em>What did it record?</em></h1><p class="hero-deck">{_html(hero_deck)}</p><div class="hero-facts">{hero_facts}</div></div>
        <aside class="hero-note"><p class="note-label">Five angles / one task</p><p>History, context, usage, portability, and storage — read side by side.</p></aside>
      </section>
      <div class="pilot-strip" role="status"><strong>Pilot / not ranked</strong><span>{_html(pilot_text)}</span></div>

      <section class="section" id="scorecard" aria-labelledby="score-heading">
        <div class="section-heading"><p class="section-kicker">01 / comparative view</p><div><h2 id="score-heading">Five angles. One coding task.</h2><p class="lede">Work history, context visibility, usage transparency, access and portability, and storage efficiency make the saved record legible. Bars show evidenced points against a fixed denominator; unresolved checks never earn displayed points.</p></div></div>
        <div class="score-grid">{_render_score_cards(report, configurations)}</div>
        {_render_score_table(report, configurations)}
      </section>

      <section class="section" id="visuals" aria-labelledby="visual-heading">
        <div class="section-heading"><p class="section-kicker">02 / shareable evidence</p><div><h2 id="visual-heading">A little more signal, at a glance.</h2><p class="lede">These two masters are sized for a phone-first share. The text table above and the evidence links below carry the detail that color cannot.</p></div></div>
        <div class="visual-grid"><figure class="visual-card"><img src="scorecard.svg" width="1200" height="675" alt="{_html(scorecard_alt)}"><figcaption class="download-row"><span>Category scorecard</span><a href="scorecard.svg" download>Download SVG</a></figcaption></figure><figure class="visual-card"><img src="composition.svg" width="1200" height="675" alt="{_html(composition_alt)}"><figcaption class="download-row"><span>Storage composition</span><a href="composition.svg" download>Download SVG</a></figcaption></figure></div>
      </section>

      <section class="section" id="composition" aria-labelledby="composition-heading">
        <div class="section-heading"><p class="section-kicker">03 / storage trace</p><div><h2 id="composition-heading">What the bytes kept.</h2><p class="lede">These are captured file sizes, not useful-payload measurements. JSONL segments classify whole records by role; SQLite pages and companions remain container bytes under structural overhead. Do not compare them as content efficiency.</p></div></div>
        <div class="composition-grid">{_render_composition_panels(configurations)}</div>
      </section>

      <section class="section" id="records" aria-labelledby="records-heading">
        <div class="section-heading"><p class="section-kicker">04 / native excerpts</p><div><h2 id="records-heading">One action, several records.</h2><p class="lede">Read what was independently observed beside what the native history retained. Locators keep each excerpt tied to a real capture boundary.</p></div></div>
        <div class="record-grid">{_render_examples(configurations)}</div>
      </section>

      <section class="section" id="method" aria-labelledby="method-heading"><details class="methodology"><summary id="method-heading"><span>05 / Method, weights, and boundaries</span><span class="serif">Open the field notes</span></summary><div class="methodology-body"><div class="method-copy">{_paragraphs(report.get("method"), "No method statement supplied.")}<p>Category values are evidenced points against a fixed denominator. A null score is an unscored observation; it is never treated as zero. Configuration rows retain collection order, and no ranking is implied.</p></div><div class="weights">{''.join(f'<div class="weight-chip"><strong>{_html(item.get("weight"), "Unknown")}</strong>{_html(item.get("name"), _string(item.get("id"), "Category"))}</div>' for item in report.get("categories", []) if isinstance(item, Mapping)) or '<div class="unknown-copy">Category weights unavailable.</div>'}</div></div></details></section>

      <section class="section" id="unknowns" aria-labelledby="unknown-heading"><details class="limitations-details"><summary id="unknown-heading"><span>06 / What remains open</span><span class="serif">Open limitations</span></summary><div class="limitations-body"><p class="lede">A pilot is useful when its edges are visible. These limitations describe what this edition did not establish.</p><ul class="unknown-list">{_render_unknowns(report, configurations)}</ul></div></details></section>
    </main>
    <footer class="footer"><span>Generated locally · {len(configurations)} configurations · {_html(run_text)}</span><nav class="footer-links" aria-label="Report downloads"><a href="report.json" download>Raw report JSON</a><a href="scorecard.svg" download>Scorecard SVG</a><a href="composition.svg" download>Composition SVG</a></nav></footer>
  </div>
  <script type="application/json" id="report-data">{report_json}</script>
  <script>
    // Keep the page useful when copied as a static artifact: native details
    // remain functional without JavaScript, and this small hook only records
    // that the optional enhancement is available for future local tooling.
    document.documentElement.dataset.staticReport = "ready";
  </script>
</body>
</html>
'''


def render_report(report: dict, output_dir: Path) -> None:
    """Write a self-contained HTML/SVG/JSON report into ``output_dir``.

    ``report`` is intentionally not mutated.  The caller owns validation and
    evidence copying; this function only renders the supplied values and
    refuses to turn absent scores, bytes, or paths into invented observations.
    """

    if not isinstance(report, Mapping):
        raise TypeError("report must be a mapping")
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    scorecard_svg = render_scorecard_svg(report)
    composition_svg = render_composition_svg(report)
    index_html = _render_html(report, scorecard_svg, composition_svg)
    output_dir.joinpath("index.html").write_text(index_html, encoding="utf-8")
    output_dir.joinpath("scorecard.svg").write_text(scorecard_svg, encoding="utf-8")
    output_dir.joinpath("composition.svg").write_text(composition_svg, encoding="utf-8")
    output_dir.joinpath("report.json").write_text(
        json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )


__all__ = ["render_report", "render_scorecard_svg", "render_composition_svg"]
