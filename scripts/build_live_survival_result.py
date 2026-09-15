#!/usr/bin/env python3
"""Build the local OpenCode survival result and five-surface status page."""

from __future__ import annotations

import hashlib
import html
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from session_bench.survival_evidence import (  # noqa: E402
    score_evidence_run,
    validate_evidence_input,
)
from session_bench.live_metric_comparator import compare_survival_run  # noqa: E402
from session_bench.survival_metrics import aggregate_configuration  # noqa: E402


RUNS = tuple(
    ROOT / f"artifacts/survival-v1-runs/opencode-cli-eval-{number}/evaluation-correction"
    for number in (1, 2, 3)
)
OUTPUT = ROOT / "artifacts/survival-v1-live-result"
CATEGORY_LABELS = {
    "work_reconstruction": "Work reconstruction",
    "causal_links": "Causal links",
    "revision_trace": "Revision trace",
    "response_attribution": "Usage & attribution",
    "portable_archive": "Portable archive",
}
CATEGORY_MAX = {
    "work_reconstruction": 35,
    "causal_links": 20,
    "revision_trace": 15,
    "response_attribution": 20,
    "portable_archive": 10,
}


def _read(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain an object")
    return value


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def _score_label(value: object) -> str:
    return f"{float(value):g}"


def _verify_package(directory: Path) -> dict:
    package = _read(directory / "package-manifest.json")
    package_core = {key: value for key, value in package.items() if key != "package_digest"}
    if hashlib.sha256(_canonical(package_core)).hexdigest() != package.get("package_digest"):
        raise ValueError(f"{directory.name}: package digest mismatch")
    listed = package.get("files")
    if not isinstance(listed, list):
        raise ValueError(f"{directory.name}: package file list is missing")
    expected_paths = {str(item.get("path")) for item in listed if isinstance(item, dict)}
    actual_paths = {
        path.relative_to(directory).as_posix()
        for path in directory.rglob("*")
        if path.is_file() and path.name != "package-manifest.json"
    }
    if expected_paths != actual_paths or len(expected_paths) != len(listed):
        raise ValueError(f"{directory.name}: package file boundary mismatch")
    for item in listed:
        path = directory / item["path"]
        if _sha(path) != item.get("sha256") or path.stat().st_size != item.get("size_bytes"):
            raise ValueError(f"{directory.name}: package artifact mismatch: {item['path']}")
    return package


def _verify_metric_locators(
    directory: Path,
    evidence: dict,
    observer: dict,
    decoded: dict,
    receipt: dict,
) -> None:
    observer_ids = {
        str(item.get("id"))
        for key in ("events", "relations")
        for item in observer.get(key, [])
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    native_records = [
        item
        for key in ("turns", "responses", "actions", "results", "file_changes", "relations", "usage")
        for item in decoded.get(key, [])
        if isinstance(item, dict)
    ]
    native_locations = {
        (locator.get("table"), locator.get("row_id"))
        for item in native_records
        if isinstance((locator := item.get("locator")), dict)
    }
    native_manifest_sha = _sha(directory / "native-manifest.json")
    receipt_sha = _sha(directory / "portability-receipt.json")
    db_sha = _sha(directory / "native-bundle/opencode.db")
    companion_shas = {
        path.name: _sha(path)
        for path in (directory / "native-bundle").iterdir()
        if path.is_file()
    }
    for row in evidence["metric_evidence"]:
        if not set(row["observer_ids"]).issubset(observer_ids):
            raise ValueError(f"{directory.name}: unknown observer evidence ID")
        for locator in row["native_locators"]:
            location = locator["record_location"]
            digest = locator["artifact_sha256"]
            if location.startswith("table:") and "/row:" in location:
                table, row_id = location[6:].split("/row:", 1)
                if digest != db_sha or (table, row_id) not in native_locations:
                    raise ValueError(f"{directory.name}: unresolved native record locator")
            elif location.startswith("scan:session/"):
                if digest != db_sha or receipt.get("complete_root") is not True:
                    raise ValueError(f"{directory.name}: invalid absence-scan locator")
            elif location.startswith("file:"):
                name = location[5:]
                if companion_shas.get(name) != digest:
                    raise ValueError(f"{directory.name}: invalid companion locator")
            elif location == "document:portability-receipt.json":
                if digest != receipt_sha:
                    raise ValueError(f"{directory.name}: invalid receipt locator")
            elif location == "scope:isolated-opencode-session-database-family":
                if digest != native_manifest_sha:
                    raise ValueError(f"{directory.name}: invalid manifest locator")
            else:
                raise ValueError(f"{directory.name}: unsupported evidence locator")


def _verify_and_score(directory: Path):
    package = _verify_package(directory)
    evidence = validate_evidence_input(_read(directory / "evidence.json"))
    observer = _read(directory / "observer.json")
    decoded = _read(directory / "decoded.json")
    receipt = _read(directory / "portability-receipt.json")
    recorded_measurement = _read(directory / "measurement.json")
    if _sha(directory / "observer.json") != evidence["observer"]["sha256"]:
        raise ValueError(f"{directory.name}: observer digest mismatch")
    if _sha(directory / "native-manifest.json") != evidence["native_manifest"]["sha256"]:
        raise ValueError(f"{directory.name}: native manifest digest mismatch")
    # Correction packages carry the exact replay runtime that produced their
    # evidence.  Verify that immutable package-local decoder, rather than the
    # mutable checkout source, so historical packages remain reproducible
    # after later decoder work.
    decoder = directory / "replay-runtime/session_bench/adapters/opencode_decoder.py"
    if not decoder.is_file() or decoder.is_symlink():
        raise ValueError(f"{directory.name}: package-local decoder is missing")
    if _sha(decoder) != evidence["decoder"]["sha256"]:
        raise ValueError(f"{directory.name}: decoder digest mismatch")
    manifest = _read(directory / "native-manifest.json")
    for name in ("decoded.json", "portability-receipt.json", "measurement.json"):
        if _sha(directory / name) != manifest.get("derived", {}).get(name):
            raise ValueError(f"{directory.name}: derived artifact digest mismatch: {name}")
    for artifact in manifest["files"]:
        path = directory / "native-bundle" / artifact["name"]
        if _sha(path) != artifact["sha256"] or path.stat().st_size != artifact["size_bytes"]:
            raise ValueError(f"{directory.name}: native artifact digest mismatch")
    identity = evidence.get("identity")
    if not isinstance(identity, dict):
        raise ValueError(f"{directory.name}: exact identity is missing")
    native_session = decoded.get("session")
    if not isinstance(native_session, dict) or native_session.get("version") != identity.get("build"):
        raise ValueError(f"{directory.name}: native build identity mismatch")
    replayed = compare_survival_run(
        observer,
        decoded,
        receipt,
        configuration_id=evidence["configuration_id"],
        repetition=evidence["repetition"],
    )
    if replayed != recorded_measurement or replayed != evidence["measurement"]:
        raise ValueError(f"{directory.name}: measurement does not reproduce")
    _verify_metric_locators(directory, evidence, observer, decoded, receipt)
    summary = _read(directory / "summary.json")
    if summary.get("result_id") != package.get("result_id"):
        raise ValueError(f"{directory.name}: result identity mismatch")
    return score_evidence_run(evidence), evidence, summary, replayed, package


def _svg(overall: str, categories: list[dict]) -> str:
    rows = []
    colors = ["#ff6652", "#346f94", "#d3942f", "#2c877e", "#826178"]
    for index, item in enumerate(categories):
        y = 348 + index * 54
        width = 620 * item["fraction"]
        rows.append(
            f'<text x="70" y="{y}" class="label">{_esc(item["name"])}</text>'
            f'<rect x="330" y="{y-20}" width="620" height="22" rx="3" fill="#ded5c8"/>'
            f'<rect x="330" y="{y-20}" width="{width:.1f}" height="22" rx="3" fill="{colors[index]}"/>'
            f'<text x="978" y="{y}" class="value">{_esc(item["score_label"])}</text>'
        )
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="675" viewBox="0 0 1200 675">
<rect width="1200" height="675" fill="#f5efe4"/><path d="M0 0h1200v12H0z" fill="#182633"/>
<style>.serif{{font-family:Georgia,serif}}.mono{{font-family:Menlo,monospace}}.label{{font:17px Menlo,monospace;fill:#182633}}.value{{font:bold 17px Menlo,monospace;fill:#182633;text-anchor:end}}</style>
<text x="70" y="72" class="mono" font-size="15" letter-spacing="3" fill="#657078">SESSION-BENCH V1 · LIVE PROTOTYPE</text>
<text x="70" y="156" class="serif" font-size="64" font-weight="700" fill="#182633">OpenCode CLI</text>
<text x="70" y="208" class="serif" font-size="31" fill="#47545a">Portable archive. Incomplete reconstruction.</text>
<text x="1018" y="172" class="serif" font-size="124" font-weight="700" text-anchor="end" fill="#ff6652">{_esc(overall)}</text>
<text x="1025" y="204" class="mono" font-size="14" text-anchor="end" fill="#657078">SESSION SURVIVAL SCORE</text>
{''.join(rows)}
<line x1="70" y1="623" x2="1130" y2="623" stroke="#182633" stroke-width="2"/>
<text x="70" y="650" class="mono" font-size="14" fill="#182633">3 / 3 ISOLATED RUNS</text>
<text x="1130" y="650" class="mono" font-size="13" text-anchor="end" fill="#657078">19 CHECKS · NO LEADERBOARD YET</text>
</svg>'''


def build() -> Path:
    scored = [_verify_and_score(directory) for directory in RUNS]
    runs = [item[0] for item in scored]
    aggregate = aggregate_configuration(runs)
    if not aggregate.rankable or aggregate.overall is None:
        raise ValueError("OpenCode aggregate is not eligible for a survival score")
    evidence = [item[1] for item in scored]
    summaries = [item[2] for item in scored]
    packages = [item[4] for item in scored]
    identities = [item.get("identity") for item in evidence]
    if any(identity != identities[0] for identity in identities[1:]):
        raise ValueError("OpenCode repetitions do not share one exact identity")
    identity = identities[0]
    if not isinstance(identity, dict):
        raise ValueError("OpenCode identity is missing")
    build = identity.get("build")
    model = identity.get("model")
    if not isinstance(build, str) or not isinstance(model, str):
        raise ValueError("OpenCode build/model identity is incomplete")
    overall = aggregate.display()["overall"]
    if not isinstance(overall, str):
        raise ValueError("OpenCode survival score is missing")
    run_overalls = [run.display()["overall"] for run in runs]
    if len(set(run_overalls)) != 1:
        raise ValueError("OpenCode repetitions do not reproduce one score")
    categories = []
    for category, maximum in CATEGORY_MAX.items():
        value = aggregate.categories[category]
        if value is None:
            raise ValueError(f"aggregate category is unresolved: {category}")
        categories.append(
            {
                "id": category,
                "name": CATEGORY_LABELS[category],
                "score": float(value),
                "maximum": maximum,
                "score_label": f"{float(value):g}/{maximum}",
                "fraction": float(value) / maximum,
            }
        )
    result = {
        "schema_version": "session-bench-v1-live-survival-result",
        "edition": "local-unreleased-prototype",
        "claim": f"OpenCode CLI {build} with {model} scored {overall}/100 on Session-Bench's Session Survival lens across three isolated two-turn runs.",
        "claim_limit": "This is the Session Survival lens, not the full 31-metric v1 product score or a cross-product rank.",
        "configuration": {
            "id": "opencode-cli",
            "product": "OpenCode CLI",
            "build": build,
            "model": model,
            "surface": "CLI",
        },
        "survival_score": {
            "overall": overall,
            "range": aggregate.display()["range"],
            "eligible": True,
            "repetitions": 3,
        },
        "categories": categories,
        "runs": [
            {
                "raw_run_id": run.run_id,
                "campaign_attempt_id": summary["attempt_id"],
                "repetition": run.repetition,
                "overall": run.display()["overall"],
                "survival_score_eligible": run.rankable,
                "evidence_sha256": _sha(directory / "evidence.json"),
                "observer_sha256": item["observer"]["sha256"],
                "native_manifest_sha256": item["native_manifest"]["sha256"],
                "package_id": summary["package_id"],
                "result_id": summary["result_id"],
                "package_digest": package["package_digest"],
                "session_id": summary["session_id"],
                "event_cost": 0,
            }
            for run, item, summary, package, directory in zip(
                runs, evidence, summaries, packages, RUNS, strict=True
            )
        ],
        "cohort": [
            {"id": "opencode-cli", "name": "OpenCode CLI", "state": "Survival verified", "detail": f"3/3 evidence-bound runs · {overall}/100 survival"},
            {"id": "cursor-cli", "name": "Cursor CLI", "state": "Paused · unscored", "detail": "one intact evaluated capture; next attempt stopped after R1; Cursor Free quota reached"},
            {"id": "codex-cli", "name": "Codex CLI", "state": "Unranked", "detail": "decoder ready; evaluated repetitions not collected"},
            {"id": "codex-desktop", "name": "Codex Desktop", "state": "Calibrated", "detail": "two-turn task-API observation, copied rollout decode, and damage control passed; evaluated runs not collected"},
            {"id": "cursor-desktop", "name": "Cursor Desktop", "state": "Paused · unscored", "detail": "one complete evaluated capture; transcript plus SQLite results decoded offline; next attempt invalid at Cursor Free usage limit; extra rows unqualified"},
        ],
        "leaderboard": {"published": False, "reason": "requires at least three qualified configurations and one qualified CLI/Desktop pair"},
        "verification": {
            "observer": "submitted prompts, visible JSON stream, helper ledger, filesystem hashes, and usage trace",
            "native": "isolated opencode.db with WAL and SHM",
            "decoder": "copied-bundle decode with earlier native files denied",
            "comparison": "report replays the frozen observer/native comparator and requires exact equality with each repetition's stored measurement; semantic score/category content matches across repetitions after excluding run identity fields",
            "qualified_strengths": ["portable archive 10/10", "causal links 20/20"],
            "recorded_gaps": [
                "three shell action rows omitted required reconstructed targets or ordered arguments",
                "native edit row retained fragments but no whole-file before/after hashes",
                "no explicit native final-after-R2 relation",
                "response-linked usage did not reconcile to native session totals",
            ],
            "independent_reproduction": False,
        },
    }
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / "result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (OUTPUT / "scorecard.svg").write_text(
        _svg(_score_label(aggregate.display()["overall"]), categories), encoding="utf-8"
    )
    bars = "".join(
        f'''<div class="bar"><div><b>{_esc(item['name'])}</b><span>{_esc(item['score_label'])}</span></div><i><em style="width:{item['fraction']*100:.1f}%"></em></i></div>'''
        for item in categories
    )
    cohort = "".join(
        f'''<tr><td>{_esc(item['name'])}</td><td><span class="pill {item['state'].lower().replace(' ', '-')}">{_esc(item['state'])}</span></td><td>{_esc(item['detail'])}</td></tr>'''
        for item in result["cohort"]
    )
    run_cards = "".join(
        f'''<article><span>RUN {item['repetition']}</span><strong>{_esc(_score_label(item['overall']))}</strong><small>{_esc(item['session_id'])}</small><small>event cost: 0</small></article>'''
        for item in result["runs"]
    )
    gap_cards = "".join(
        f"<li>{_esc(item)}</li>" for item in result["verification"]["recorded_gaps"]
    )
    page = f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="icon" href="data:,"><title>Session-Bench v1 · OpenCode survival result</title><style>
:root{{--paper:#f5efe4;--ink:#182633;--muted:#657078;--line:#d5cbbb;--coral:#ff6652;--blue:#346f94;--green:#26735b;--amber:#a87313}}*{{box-sizing:border-box}}body{{margin:0;background:var(--paper);color:var(--ink);font-family:Menlo,Monaco,monospace;background-image:radial-gradient(#1826330d 1px,transparent 1px);background-size:15px 15px}}main{{width:min(1160px,calc(100% - 34px));margin:auto;padding:28px 0 72px}}header{{display:flex;justify-content:space-between;border-bottom:2px solid var(--ink);padding-bottom:15px;font-size:11px;letter-spacing:.13em}}.hero{{display:grid;grid-template-columns:1.25fr .75fr;gap:42px;padding:68px 0 52px;border-bottom:1px solid var(--line)}}.k{{font-size:10px;letter-spacing:.15em;color:var(--muted)}}h1,h2{{font-family:Georgia,serif;margin:0}}h1{{font-size:clamp(62px,9vw,126px);line-height:.82;letter-spacing:-.065em}}h1 span{{color:var(--coral)}}.dek{{font:26px/1.25 Georgia,serif;max-width:700px}}.score{{align-self:end;border:2px solid var(--ink);padding:24px;background:#fffaf0;box-shadow:9px 9px 0 var(--ink)}}.score strong{{display:block;font:800 94px/.82 Georgia,serif;color:var(--coral)}}.score small{{line-height:1.5}}.quote{{font:600 clamp(24px,3.8vw,46px)/1.08 Georgia,serif;padding:34px 0;border-bottom:1px solid var(--line)}}.quote em{{color:var(--green);font-style:normal}}section{{padding:48px 0;border-bottom:1px solid var(--line)}}h2{{font-size:52px;letter-spacing:-.035em}}.grid{{display:grid;grid-template-columns:1fr 1fr;gap:54px}}.bar{{margin:18px 0}}.bar div{{display:flex;justify-content:space-between;font-size:12px}}.bar i{{display:block;background:#ded5c8;height:16px;margin-top:8px}}.bar em{{display:block;height:100%;background:var(--blue)}}.runs{{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}}.runs article{{border:1px solid var(--ink);background:#fffaf0;padding:18px}}.runs span,.runs small{{display:block;font-size:9px;color:var(--muted);overflow-wrap:anywhere}}.runs strong{{display:block;font:700 48px Georgia,serif;color:var(--green);margin:9px 0}}.gaps{{margin:28px 0 0;padding:0;list-style:none}}.gaps li{{border-top:1px solid var(--line);padding:12px 0;font-size:11px;line-height:1.5}}table{{width:100%;border-collapse:collapse;margin-top:25px}}td{{border-top:1px solid var(--line);padding:15px 8px;font-size:11px}}td:first-child{{font:600 20px Georgia,serif}}.pill{{padding:6px 8px;border:1px solid currentColor;font-size:9px;white-space:nowrap}}.survival-verified{{color:var(--green)}}.unranked{{color:var(--muted)}}.blocked{{color:var(--amber)}}.chain{{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-top:25px}}.chain div{{background:var(--ink);color:var(--paper);padding:18px;min-height:120px}}.chain b{{display:block;color:#ffd064;margin-bottom:12px}}.limit{{background:var(--coral);color:#fff;padding:25px;font:20px/1.35 Georgia,serif;margin-top:28px}}footer{{padding-top:24px;font-size:10px;color:var(--muted);display:flex;justify-content:space-between;gap:24px}}@media(max-width:780px){{.hero,.grid{{grid-template-columns:1fr}}.runs,.chain{{grid-template-columns:1fr}}h2{{font-size:38px}}td{{display:block}}footer{{display:block}}}}
</style><style>.calibrated{{color:var(--blue)}}</style></head><body><main><header><span>SESSION-BENCH / V1</span><span>LOCAL · UNRELEASED · LIVE EVIDENCE</span></header><div class="hero"><div><p class="k">FIRST QUALIFIED SURVIVAL CONFIGURATION</p><h1>OPEN<br><span>CODE</span></h1><p class="dek">All three copied SQLite bundles decoded offline. Causality and portability survived; several exact reconstruction assertions did not.</p></div><div class="score"><strong>{_esc(overall)}</strong><small>SESSION SURVIVAL SCORE<br>3 / 3 ISOLATED RUNS</small></div></div><div class="quote">“OpenCode’s archive was <em>100% portable</em> — and {_esc(overall)}/100 reconstructable.”</div><section class="grid"><div><p class="k">FIVE ANGLES · 19 CHECKS</p><h2>What survived.</h2>{bars}<p class="k" style="margin-top:34px">WHERE POINTS WERE LOST</p><ul class="gaps">{gap_cards}</ul></div><div><p class="k">REPETITIONS</p><h2>Same score, three times.</h2><div class="runs">{run_cards}</div></div></section><section><p class="k">THE FIVE-SURFACE PROTOTYPE</p><h2>One score. Four honest blanks.</h2><table>{cohort}</table></section><section><p class="k">EVIDENCE CHAIN</p><h2>The report replays the score.</h2><div class="chain"><div><b>01 · OBSERVE</b>Prompts, visible stream, helpers, files, usage.</div><div><b>02 · CAPTURE</b>Isolated SQLite plus WAL and SHM.</div><div><b>03 · DECODE</b>Copied bundle; earlier native files denied.</div><div><b>04 · REPLAY</b>Manifest hashes, comparator, and measurement must agree.</div></div><div class="limit"><b>Quote it precisely:</b> {_esc(result['claim'])} {_esc(result['claim_limit'])}</div></section><footer><span>{_esc(f'OPENCODE {build} · {model}')}</span><span>NO CROSS-PRODUCT LEADERBOARD YET</span></footer></main></body></html>'''
    (OUTPUT / "index.html").write_text(page, encoding="utf-8")
    return OUTPUT


if __name__ == "__main__":
    print(build() / "index.html")
