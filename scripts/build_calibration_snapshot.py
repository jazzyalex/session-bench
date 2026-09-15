#!/usr/bin/env python3
"""Build the visual five-surface calibration snapshot; never launch a vendor."""

from __future__ import annotations

import argparse
import html
import json
from pathlib import Path


def esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def build(source: Path, destination: Path) -> None:
    data = json.loads(source.read_text(encoding="utf-8"))
    if data.get("schema_version") != "1.0-calibration-snapshot":
        raise ValueError("unsupported calibration snapshot")
    surfaces = data.get("surfaces")
    categories = data.get("categories")
    if not isinstance(surfaces, list) or len(surfaces) != 5:
        raise ValueError("snapshot must contain exactly five surfaces")
    if not isinstance(categories, list) or sum(item["points"] for item in categories) != 100:
        raise ValueError("snapshot category weights must total 100")
    footer_notice = data.get("footer_notice", "LOCAL · UNRELEASED · NO VENDOR RANKING")
    if not isinstance(footer_notice, str) or not footer_notice.strip():
        raise ValueError("snapshot footer_notice must be a non-empty string")
    destination.mkdir(parents=True, exist_ok=True)
    cards = []
    for index, row in enumerate(surfaces, 1):
        proof = "".join(f"<li>{esc(item)}</li>" for item in row["proof"])
        cards.append(f'''<article class="surface-card state-{esc(row['state'])}" style="--i:{index}">
          <div class="surface-number">0{index}</div><div class="surface-main">
          <div class="surface-head"><div><p class="eyebrow">{esc(row['configuration_id'])}</p><h3>{esc(row['name'])}</h3><p class="build">{esc(row['build'])}</p></div><span class="state">{esc(row['state_label'])}</span></div>
          <ul class="proof">{proof}</ul>
          <div class="finding"><p><b>Why no score</b>{esc(row['blocker'])}</p><p><b>Next gate</b>{esc(row['next'])}</p></div>
          </div></article>''')
    angle_cells = "".join(
        f'''<div class="angle" style="--accent:{esc(item['color'])};--width:{item['points']}%"><span>{esc(item['name'])}</span><strong>{item['points']}</strong></div>'''
        for item in categories
    )
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    page = f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Session-Bench v1 · calibration snapshot</title>
<style>
:root{{--paper:#f4efe5;--ink:#17201f;--muted:#66706d;--line:#c9c1b4;--red:#ff5e45;--green:#34765a;--amber:#ad7616;--blue:#425f9d}}
*{{box-sizing:border-box}} body{{margin:0;color:var(--ink);background:var(--paper);font-family:'SFMono-Regular',Menlo,Monaco,monospace;background-image:radial-gradient(#17201f10 1px,transparent 1px);background-size:14px 14px}}
body:before{{content:'';position:fixed;inset:0;pointer-events:none;background:linear-gradient(115deg,#ff5e4510,transparent 32%,#34765a0d 67%,transparent)}}
.page{{width:min(1180px,calc(100% - 34px));margin:auto;padding:28px 0 70px}} .mast{{display:flex;justify-content:space-between;gap:20px;border-bottom:2px solid var(--ink);padding:0 0 15px;font-size:11px;letter-spacing:.12em}}
.hero{{display:grid;grid-template-columns:1.45fr .55fr;gap:44px;padding:68px 0 52px;border-bottom:1px solid var(--line)}} .kicker,.eyebrow{{font-size:10px;letter-spacing:.16em;text-transform:uppercase;color:var(--muted);margin:0 0 12px}}
h1,h2,h3{{font-family:'Iowan Old Style',Baskerville,Georgia,serif;margin:0}} h1{{font-size:clamp(66px,10vw,142px);line-height:.78;letter-spacing:-.065em}} h1 span{{color:var(--red)}} .dek{{font-family:'Iowan Old Style',Baskerville,Georgia,serif;font-size:clamp(20px,2.4vw,32px);line-height:1.2;max-width:700px;margin:32px 0 0}}
.score-zero{{align-self:end;border:2px solid var(--ink);padding:24px;background:#fffaf0;box-shadow:9px 9px 0 var(--ink)}} .score-zero strong{{display:block;font:800 100px/.8 'Iowan Old Style',Baskerville,Georgia,serif;color:var(--red)}} .score-zero span{{font-size:12px;font-weight:500}}
.quote{{padding:32px 0;font:600 clamp(24px,4vw,48px)/1.05 'Iowan Old Style',Baskerville,Georgia,serif;border-bottom:1px solid var(--line)}} .quote mark{{color:var(--green);background:none}}
.angles{{padding:42px 0}} .section-head{{display:flex;align-items:end;justify-content:space-between;gap:20px;margin-bottom:22px}} h2{{font-size:clamp(34px,5vw,66px);letter-spacing:-.035em}} .section-head p{{max-width:480px;color:var(--muted);font-size:12px;line-height:1.6}}
.angle-grid{{display:grid;grid-template-columns:repeat(5,1fr);border:2px solid var(--ink)}} .angle{{min-height:170px;padding:18px;border-right:1px solid var(--ink);position:relative;background:#fffaf2}} .angle:last-child{{border:0}} .angle:before{{content:'';position:absolute;left:0;right:0;bottom:0;height:var(--width);background:var(--accent)}} .angle span,.angle strong{{position:relative;z-index:1}} .angle span{{display:block;font-size:10px;text-transform:uppercase;line-height:1.4;letter-spacing:.08em}} .angle strong{{display:block;font:800 52px/1 'Iowan Old Style',Baskerville,Georgia,serif;margin-top:19px}}
.surfaces{{padding:54px 0}} .surface-list{{border-top:2px solid var(--ink)}} .surface-card{{display:grid;grid-template-columns:85px 1fr;border-bottom:1px solid var(--ink);padding:28px 0;animation:rise .55s both;animation-delay:calc(var(--i)*.06s)}} .surface-number{{font:600 30px/1 'Iowan Old Style',Baskerville,Georgia,serif;color:var(--muted)}} .surface-head{{display:flex;justify-content:space-between;gap:20px}} h3{{font-size:34px}} .build{{font-size:11px;color:var(--muted);margin:5px 0}} .state{{align-self:start;border:1px solid currentColor;padding:7px 9px;font-size:9px;letter-spacing:.1em}} .state-captured .state{{color:var(--green)}} .state-blocked .state{{color:var(--amber)}} .state-invalid .state{{color:#b44537}}
.proof{{display:flex;flex-wrap:wrap;gap:8px;list-style:none;padding:0;margin:20px 0}} .proof li{{border-radius:20px;background:#17201f;color:#fff;padding:7px 11px;font-size:9px}} .finding{{display:grid;grid-template-columns:1fr 1fr;gap:26px}} .finding p{{margin:0;color:var(--muted);font-size:11px;line-height:1.55}} .finding b{{display:block;color:var(--ink);text-transform:uppercase;font-size:9px;letter-spacing:.12em;margin-bottom:6px}}
.rule{{margin-top:36px;border:2px solid var(--ink);padding:27px;background:var(--ink);color:var(--paper);display:grid;grid-template-columns:1fr 1fr;gap:35px}} .rule h2{{color:#ffcf5b}} .rule p{{font-size:12px;line-height:1.7;margin:0}} footer{{display:flex;justify-content:space-between;gap:15px;padding-top:25px;font-size:9px;color:var(--muted)}}
@keyframes rise{{from{{opacity:0;transform:translateY(12px)}}to{{opacity:1;transform:none}}}} @media(max-width:800px){{.hero{{grid-template-columns:1fr}}.score-zero{{width:210px}}.angle-grid{{grid-template-columns:1fr}}.angle{{min-height:95px;border-right:0;border-bottom:1px solid var(--ink)}}.surface-card{{grid-template-columns:45px 1fr}}.surface-head,.finding,.rule{{grid-template-columns:1fr;display:grid}}}}
@media(prefers-reduced-motion:reduce){{*{{animation:none!important}}}}
</style></head><body><main class="page"><header class="mast"><span>SESSION-BENCH / V1</span><span>{esc(data['edition_label'])}</span></header>
<section class="hero"><div><p class="kicker">A benchmark that makes its uncertainty visible</p><h1>SESSION<br><span>BENCH</span></h1><p class="dek">Five surfaces. Five angles. Thirty-one evidence-backed checks. The ranking stays blank until the record earns it.</p></div><div class="score-zero"><strong>{data['published_scores']}</strong><span>PUBLISHED VENDOR SCORES</span></div></section>
<div class="quote">“Five surfaces entered. <mark>Zero scores escaped without evidence.</mark>”</div>
<section class="angles"><div class="section-head"><div><p class="kicker">THE PUBLIC SHAPE</p><h2>One quotable /100.<br>Five honest angles.</h2></div><p>The color is for comprehension. The 31 metric cells, copied native evidence, and negative controls supply the depth behind each bar.</p></div><div class="angle-grid">{angle_cells}</div></section>
<section class="surfaces"><div class="section-head"><div><p class="kicker">LIVE CALIBRATION</p><h2>What survived.<br>What still blocks.</h2></div><p>These are acquisition states, not product scores. Captured means evidence exists. Scored requires three frozen repetitions and a working offline decoder.</p></div><div class="surface-list">{''.join(cards)}</div></section>
<section class="rule"><h2>Captured ≠ scored.</h2><p>That single distinction is the upgrade from v0.4: the report can stay bright and easy to quote while every number has a visible chain from observed action to native record to offline reconstruction.</p></section>
<footer><span>{esc(data['generated_from'])}</span><span>{esc(footer_notice)}</span></footer></main></body></html>'''
    (destination / "index.html").write_text(page, encoding="utf-8")
    (destination / "snapshot.json").write_text(payload + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=Path("plans/survival-v1/calibration-snapshot.json"))
    parser.add_argument("--out", type=Path, default=Path("artifacts/survival-v1-calibration"))
    args = parser.parse_args()
    build(args.source, args.out)


if __name__ == "__main__":
    main()
