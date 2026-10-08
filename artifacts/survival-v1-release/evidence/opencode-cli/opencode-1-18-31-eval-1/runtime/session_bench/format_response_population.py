"""Observer-bound response populations for broad readable-rationale evidence.

Required IDs come from the independently captured observer, never from the
surviving native responses. Matching reuses the survival comparator's native
canary/turn semantics and additionally requires the observed text (see
``observed_response_text_matches``, shared with the timestamp evidence).
Every decoded final-response occurrence remains in the output denominator.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

from .live_metric_comparator import (
    _embedded_canary, _field, _match_many, _match_response, _match_turn,
    _native_id, _native_responses, _native_supported, _native_turns,
    _observer_events, _turn_id, _validate_observer,
    _SEVERE_DIAGNOSTICS,
)


class ObserverResponseEvidenceError(ValueError):
    """The supplied observer bytes are not bound to the declared observer ID."""


def _bound_observer(document: bytes, reference: Mapping[str, Any], run_id: str) -> Mapping[str, Any]:
    if (not isinstance(reference, Mapping) or set(reference) != {"id", "sha256"}
            or not isinstance(reference["id"], str) or not reference["id"].strip()):
        raise ObserverResponseEvidenceError("observer reference must bind an ID and SHA-256")
    if not isinstance(document, bytes):
        raise ObserverResponseEvidenceError("observer_document must be the exact serialized bytes")
    if hashlib.sha256(document).hexdigest() != reference.get("sha256"):
        raise ObserverResponseEvidenceError("observer_document digest does not match the bound observer")

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ObserverResponseEvidenceError("observer_document contains a duplicate JSON key")
            result[key] = value
        return result

    def reject_constant(value):
        raise ObserverResponseEvidenceError(f"observer_document contains a non-finite number: {value}")

    try:
        observer = json.loads(document, object_pairs_hook=pairs, parse_constant=reject_constant)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ObserverResponseEvidenceError("observer_document must contain valid JSON") from error
    observed_run, _, _ = _validate_observer(observer)
    if observed_run != run_id:
        raise ObserverResponseEvidenceError("observer_document run_id differs from format evidence")
    return observer


def observed_response_text_matches(fields: Mapping[str, Any], text: Any) -> bool:
    """One response text rule for the rationale and the timestamp evidence.

    The native text must equal the observed text. An observer that witnessed
    only the response canary cannot require the generated prose around it: its
    text is the canary alone, and then the canary must close the native
    response. Observed prose that differs from the native prose never matches.
    """
    expected = fields.get("text")
    if text == expected:
        return True
    if not isinstance(expected, str) or not isinstance(text, str):
        return False
    canary = fields.get("canary", fields.get("response_canary"))
    return bool(expected.strip()) and expected == canary and text.rstrip().endswith(expected)


def build_observer_rationale_evidence(
    decoded: Mapping[str, Any], *, observer: Mapping[str, Any], run_id: str,
    observer_document: bytes | None = None,
    complete_record_family: bool = False,
) -> dict[str, Any]:
    """Return the existing format-detail shape with an independent denominator.

    Supplying only an observer digest cannot establish its response population.
    Missing observer bytes, observed prose, or semantic response identity leaves
    the metric unresolved. Invalid digest or cross-run input is rejected.
    This helper reads no files and never supplies observer prose to a decoder.
    The caller must explicitly establish a complete captured record family;
    otherwise a missing native response is an evidence gap, never a zero.
    """
    if not isinstance(complete_record_family, bool):
        raise ObserverResponseEvidenceError("complete_record_family must be boolean")
    if observer_document is None:
        return {"evidence_complete": False, "response_ids": [], "records": []}
    value = _bound_observer(observer_document, observer, run_id)
    _, events, _ = _validate_observer(value)
    expected = _observer_events(events, "assistant_response")
    required = [row["id"] for row in expected]
    streams = [decoded[key] for key in ("responses", "visible_responses") if key in decoded]
    facts = decoded.get("facts")
    if not streams and isinstance(facts, Mapping):
        streams = [facts[key] for key in ("responses", "visible_responses") if key in facts]
    if not streams:
        streams = [decoded[key] for key in ("events", "records") if key in decoded]
    stream_declared = bool(streams) and all(
        isinstance(stream, list) and all(isinstance(row, Mapping) for row in stream)
        for stream in streams
    )
    diagnostics = decoded.get("diagnostics", [])
    clean = isinstance(diagnostics, list) and not any(
        not isinstance(row, Mapping) or row.get("code") in _SEVERE_DIAGNOSTICS
        or row.get("severity") == "error" for row in diagnostics
    )
    complete = complete_record_family and bool(expected) and stream_declared and clean and _native_supported(decoded)
    for row in expected:
        fields = row["fields"]
        text = fields.get("text")
        canary = fields.get("canary", fields.get("response_canary"))
        turn = fields.get("turn_id")
        if (not isinstance(text, str) or not text.strip()
                or not ((isinstance(canary, str) and canary.strip())
                        or (isinstance(turn, str) and turn.strip()))):
            complete = False

    # Native turn IDs may differ from the observer's IDs. Only independently
    # matched user turns establish aliases; positional guesses are forbidden.
    _, _, matched_turns, _ = _match_many(
        _observer_events(events, "user_turn"), _native_turns(decoded), _match_turn,
    )
    turn_map: dict[str, str] = {}
    ambiguous: set[str] = set()
    for expected_id, candidate in matched_turns.items():
        for native_id in (_native_id(candidate), _turn_id(candidate)):
            if native_id:
                if native_id in turn_map and turn_map[native_id] != expected_id:
                    ambiguous.add(native_id)
                turn_map[native_id] = expected_id
    for native_id in ambiguous:
        turn_map.pop(native_id)

    records = []
    for ordinal, candidate in enumerate(_native_responses(decoded)):
        text = _field(candidate, "text", "content")
        candidate_canary = _field(candidate, "canary", "response_canary") or _embedded_canary(text)
        native_turn = _turn_id(candidate)
        matched_ids = []
        for row in expected:
            fields = row["fields"]
            observed_canary = fields.get("canary", fields.get("response_canary"))
            observed_turn = fields.get("turn_id")
            canary_matches = isinstance(observed_canary, str) and bool(observed_canary) and candidate_canary == observed_canary
            turn_matches = bool(native_turn and observed_turn and (
                native_turn == observed_turn or turn_map.get(native_turn) == observed_turn
            ))
            if (candidate.get("state") in (None, "present") and (canary_matches or turn_matches)
                    and _match_response(row, candidate, turn_map).value is True and observed_response_text_matches(fields, text)):
                matched_ids.append(row["id"])
        # A single native occurrence cannot replace two expected responses.
        # Ambiguous identity is retained as an unmatched occurrence.
        if len(matched_ids) == 1:
            record_id = matched_ids[0]
        else:
            record_id = f"unmatched-native-response:{ordinal}"
            while record_id in required:
                record_id += ":unmatched"
        records.append({"id": record_id, "ordered_text": text if isinstance(text, str) else ""})
    return {"evidence_complete": complete, "response_ids": required, "records": records}
