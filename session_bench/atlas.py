"""Validation and deterministic rendering for the independent format atlas."""

from datetime import date

from .schema import validate_named


REQUIRED_CLAIM_SUBJECTS = {
    "surface_documentation",
    "artifact_documentation",
    "writer_behavior",
    "decoder_correctness",
    "reproduction",
}
PUBLIC_EVIDENCE = {"public_documentation", "public_repository", "public_protocol"}


def _unique(items, key, context):
    values = [item[key] for item in items]
    if len(values) != len(set(values)):
        raise ValueError(f"duplicate {key} in {context}")


def _date(value, context):
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid {context} date: {value!r}") from exc


def validate_atlas(atlas):
    """Validate schema plus the claim/evidence separation the atlas promises."""
    validate_named(atlas, "atlas")
    entries = atlas["entries"]
    _unique(entries, "entry_id", "atlas")
    surfaces = {entry["identity"]["surface"] for entry in entries}
    if not {"cli", "desktop", "ide"}.issubset(surfaces):
        raise ValueError("atlas must include CLI, desktop, and IDE surface identities")

    any_measured = False
    for entry in entries:
        entry_id = entry["entry_id"]
        sources = entry["sources"]
        claims = entry["claims"]
        _unique(sources, "id", f"atlas entry {entry_id} sources")
        _unique(claims, "id", f"atlas entry {entry_id} claims")
        source_ids = {source["id"] for source in sources}
        subjects = {claim["subject"] for claim in claims}
        missing = REQUIRED_CLAIM_SUBJECTS - subjects
        if missing:
            raise ValueError(f"atlas entry {entry_id} lacks claim partitions: {sorted(missing)}")
        for claim in claims:
            dangling = set(claim["source_ids"]) - source_ids
            if dangling:
                raise ValueError(f"atlas entry {entry_id} has dangling source IDs: {sorted(dangling)}")
            if claim["state"] == "documented":
                if claim["evidence_kind"] not in PUBLIC_EVIDENCE or not claim["source_ids"]:
                    raise ValueError("documented claim requires cited public evidence")
            elif claim["evidence_kind"] in PUBLIC_EVIDENCE and claim["subject"] in {
                "writer_behavior", "decoder_correctness", "reproduction"
            }:
                raise ValueError("public documentation cannot establish observed behavior, decoder correctness, or reproduction")

        maintenance = entry["maintenance"]
        inspected = _date(maintenance["source_inspected_at"], "source inspection")
        due = _date(maintenance["next_inspection_due"], "next inspection")
        if due < inspected:
            raise ValueError("next inspection date precedes source inspection")
        for source in sources:
            if _date(source["inspected_at"], "source") > inspected:
                raise ValueError("entry source inspection date predates a cited source")

        correction = entry["correction"]
        if correction["status"] == "none" and any(
            correction[key] is not None for key in ("issue_url", "supersedes_entry_id")
        ):
            raise ValueError("correction state none cannot carry correction references")
        if correction["status"] != "none" and correction["issue_url"] is None:
            raise ValueError("active or resolved correction requires an issue URL")
        supersedes = correction["supersedes_entry_id"]
        if supersedes == entry_id:
            raise ValueError("atlas correction cannot supersede itself")
        if supersedes is not None and supersedes not in {item["entry_id"] for item in entries}:
            raise ValueError("atlas correction has a dangling superseded entry")

        if entry["status"] == "documented_candidate":
            if entry["measurement"] is not None or maintenance["live_tested_at"] is not None:
                raise ValueError("documentation-only candidate cannot carry live measurement evidence")
            if entry["identity"]["identity_basis"] != "public_documentation":
                raise ValueError("documentation-only candidate requires public-documentation identity basis")
            family = entry["artifact_family"]
            if family["identity_state"] == "measured" or family["acquisition"] != "public_documentation_only":
                raise ValueError("documentation-only candidate cannot claim measured/native artifact acquisition")
            for claim in claims:
                if claim["subject"] in {"writer_behavior", "decoder_correctness", "reproduction"}:
                    if claim["state"] not in {"unknown", "not_tested"} or claim["evidence_kind"] != "none":
                        raise ValueError("candidate cannot claim observed behavior, decoder correctness, or reproduction")
        elif entry["status"] == "measured":
            any_measured = True
            if entry["measurement"] is None or maintenance["live_tested_at"] is None:
                raise ValueError("measured entry requires measurement identities and live test date")
            measurement = entry["measurement"]
            for key in ("run_ids", "capture_ids", "evaluation_ids", "evidence_bundle_urls"):
                if len(measurement[key]) != len(set(measurement[key])):
                    raise ValueError(f"measured entry has duplicate {key}")
            if entry["identity"]["identity_basis"] == "public_documentation":
                raise ValueError("measured entry requires native inspection or independent reproduction identity")
            family = entry["artifact_family"]
            if family["identity_state"] == "unknown" or family["acquisition"] not in {
                "native_local_capture", "explicit_export"
            }:
                raise ValueError("measured entry requires an identified native-capture or export artifact family")

    if atlas["edition_status"] == "documentation_only" and any_measured:
        raise ValueError("documentation-only atlas cannot contain measured entries")
    if atlas["edition_status"] == "measured" and not any_measured:
        raise ValueError("measured atlas must contain measured evidence")
    return atlas


def _escape(value):
    return str(value).replace("|", "\\|").replace("\n", " ")


def render_atlas(atlas, as_of):
    """Render a reviewable snapshot; as_of is explicit for reproducible freshness labels."""
    validate_atlas(atlas)
    as_of_date = _date(as_of, "render as-of")
    lines = [
        "# Session-Bench format atlas",
        "",
        f"Snapshot date: **{as_of_date.isoformat()}**. Release: **{atlas['release_status']}**. "
        f"Edition: **{atlas['edition_status']}**.",
        "",
        "**Unreleased candidate documentation inventory. It contains no vendor result or qualification.**",
        "",
        atlas["purpose"].capitalize() + ".",
        "",
        "Machine-readable source: [`atlas/v1/index.json`](../../atlas/v1/index.json). "
        "Contract: [`schemas/v1/atlas.schema.json`](../../schemas/v1/atlas.schema.json).",
        "",
        "Reproduce this snapshot:",
        "",
        "```sh",
        "python3 -m session_bench validate-atlas atlas/v1/index.json",
        f"python3 -m session_bench render-atlas atlas/v1/index.json --as-of {as_of_date.isoformat()} --out docs/atlas/README.md",
        "```",
        "",
        "| Surface | Candidate configuration | Artifact family | Source review | Freshness | Live test |",
        "|---|---|---|---|---|---|",
    ]
    for entry in sorted(atlas["entries"], key=lambda item: item["entry_id"]):
        identity = entry["identity"]
        maintenance = entry["maintenance"]
        due = _date(maintenance["next_inspection_due"], "next inspection")
        freshness = "stale" if as_of_date > due else "current"
        family = entry["artifact_family"]
        family_label = family["family_id"] or f"{family['identity_state']}"
        live = maintenance["live_tested_at"] or "not tested"
        lines.append(
            f"| {identity['surface']} | {_escape(identity['product'])} / {_escape(identity['launch_mode'])} "
            f"| {_escape(family_label)} | {maintenance['source_inspected_at']} | {freshness}; due {due.isoformat()} | {live} |"
        )
    for entry in sorted(atlas["entries"], key=lambda item: item["entry_id"]):
        identity = entry["identity"]
        lines += [
            "",
            f"## {identity['product']}",
            "",
            f"Entry ID: `{entry['entry_id']}`. Status: **{entry['status']}**.",
            "",
            f"Identity: `{identity['surface']}` / `{identity['launch_mode']}` / `{identity['os']}`; "
            f"version: `{identity['application_version'] or 'unknown'}`; provider/model: `{identity['model_provider']}`.",
            "",
            "| Claim partition | State | Evidence | Statement |",
            "|---|---|---|---|",
        ]
        for claim in entry["claims"]:
            lines.append(
                f"| {claim['subject']} | {claim['state']} | {claim['evidence_kind']} | {_escape(claim['statement'])} |"
            )
        lines += ["", "Sources:", ""]
        for source in entry["sources"]:
            lines.append(f"- [{source['title']}]({source['url']}) — {source['locator']} (inspected {source['inspected_at']})")
        lines += ["", "Open format questions:", ""]
        for unknown in entry["artifact_family"]["unknowns"]:
            lines.append(f"- {_escape(unknown)}")
    lines += [
        "",
        "## Corrections and maintenance",
        "",
        "Each entry records its owner, source-inspection date, live-test date, and next review date in the machine-readable atlas. "
        "Disputes should cite an entry or evaluation ID and contradictory evidence; corrections retain the prior identity and link any superseding entry or evaluation.",
        "",
        "The atlas is maintained independently of conformance releases. A documented capability does not establish writer behavior, decoder correctness, or independent reproduction.",
        "",
    ]
    return "\n".join(lines)
