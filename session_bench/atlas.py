"""Validation and deterministic rendering for the independent format atlas."""

from datetime import date

from .schema import validate_named


REQUIRED_CLAIM_SUBJECTS = {
    "surface_documentation",
    "artifact_documentation",
    "representability",
    "writer_behavior",
    "decoder_correctness",
    "reproduction",
}
PUBLIC_EVIDENCE = {"public_documentation", "public_repository", "public_protocol"}
SOURCE_KIND_BY_EVIDENCE = {
    "public_documentation": "official_documentation",
    "public_repository": "public_repository",
    "public_protocol": "public_protocol",
}


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

    for entry in entries:
        entry_id = entry["entry_id"]
        sources = entry["sources"]
        claims = entry["claims"]
        _unique(sources, "id", f"atlas entry {entry_id} sources")
        _unique(claims, "id", f"atlas entry {entry_id} claims")
        source_by_id = {source["id"]: source for source in sources}
        subjects = [claim["subject"] for claim in claims]
        if set(subjects) != REQUIRED_CLAIM_SUBJECTS or len(subjects) != len(REQUIRED_CLAIM_SUBJECTS):
            raise ValueError(f"atlas entry {entry_id} must contain exactly one of every claim partition")
        for claim in claims:
            dangling = set(claim["source_ids"]) - set(source_by_id)
            if dangling:
                raise ValueError(f"atlas entry {entry_id} has dangling source IDs: {sorted(dangling)}")
            if claim["state"] == "documented":
                if claim["evidence_kind"] not in PUBLIC_EVIDENCE or not claim["source_ids"]:
                    raise ValueError("documented claim requires cited public evidence")
                expected_kind = SOURCE_KIND_BY_EVIDENCE[claim["evidence_kind"]]
                if any(source_by_id[source_id]["source_kind"] != expected_kind
                       for source_id in claim["source_ids"]):
                    raise ValueError("claim evidence kind does not match its cited source kind")
            elif claim["state"] in {"unknown", "not_tested"} and (
                claim["evidence_kind"] != "none" or claim["source_ids"]
            ):
                raise ValueError("unknown or untested claim cannot carry evidence")

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
            if maintenance["inspection_method"] != "public_source_review":
                raise ValueError("documentation-only candidate requires public source review")
            family = entry["artifact_family"]
            if ("measured" in {family["identity_state"], family["surface_marker"], family["retention"]}
                    or family["acquisition"] != "public_documentation_only"):
                raise ValueError("documentation-only candidate cannot claim measured/native artifact acquisition")
            for claim in claims:
                if claim["state"] in {"pass", "fail", "unresolved"}:
                    raise ValueError("documentation-only candidate cannot carry result states")
                if claim["evidence_kind"] not in PUBLIC_EVIDENCE | {"none"}:
                    raise ValueError("documentation-only candidate cannot carry measurement evidence")
                if claim["subject"] in {"writer_behavior", "decoder_correctness", "reproduction"}:
                    if claim["state"] not in {"unknown", "not_tested"} or claim["evidence_kind"] != "none":
                        raise ValueError("candidate cannot claim observed behavior, decoder correctness, or reproduction")
            artifact_claim = next(claim for claim in claims if claim["subject"] == "artifact_documentation")
            for root in family["roots"]:
                if not root["source_ids"] or not set(root["source_ids"]).issubset(set(artifact_claim["source_ids"])):
                    raise ValueError("documented artifact root requires sources from the artifact claim")
            if family["identity_state"] == "documented":
                if artifact_claim["state"] != "documented":
                    raise ValueError("documented artifact family requires a documented artifact claim")
                if not any((family["family_id"], family["physical_encoding"], family["tables_or_record_families"],
                            family["roots"], family["session_id_fields"], family["join_keys"])):
                    raise ValueError("documented artifact family requires at least one concrete format field")
            elif (any((family["family_id"], family["physical_encoding"], family["tables_or_record_families"],
                       family["roots"], family["session_id_fields"], family["join_keys"]))
                  or family["surface_marker"] != "unknown" or family["retention"] != "unknown"):
                raise ValueError("unknown artifact family cannot carry concrete format fields")
    return atlas


def _escape(value):
    return str(value).replace("|", "\\|").replace("\n", " ")


def render_atlas(atlas, as_of):
    """Render a reviewable snapshot; as_of is explicit for reproducible freshness labels."""
    validate_atlas(atlas)
    as_of_date = _date(as_of, "render as-of")
    latest_inspection = max(
        _date(entry["maintenance"]["source_inspected_at"], "source inspection")
        for entry in atlas["entries"]
    )
    if as_of_date < latest_inspection:
        raise ValueError("render as-of date precedes atlas source inspection")
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
            _render_correction(entry["correction"]),
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


def _render_correction(correction):
    if correction["status"] == "none":
        return "Correction status: **none**."
    issue = f"[dispute record]({correction['issue_url']})"
    supersedes = correction["supersedes_entry_id"] or "none"
    return f"Correction status: **{correction['status']}**; {issue}; supersedes: `{supersedes}`."
