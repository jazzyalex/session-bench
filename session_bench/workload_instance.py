"""Instantiate the frozen survival-v1 workload with one per-attempt run canary."""

from __future__ import annotations

from copy import deepcopy
import re
from typing import Any, Mapping


RUN_CANARY_ENV = "SB_SURVIVAL_V1_RUN_CANARY"
RUN_CANARY_PREFIX = "SB_SURVIVAL_V1_RUN_"
_SLUG = re.compile(r"^[A-Za-z0-9_-]+$")


def instantiate_workload(template: Mapping[str, Any], run_slug: str) -> tuple[dict[str, Any], dict[str, str]]:
    """Return a run-specific prompt instance without changing the workload semantics."""

    if not isinstance(template, Mapping) or template.get("schema_version") != "1.0-survival-workload":
        raise ValueError("unsupported survival workload template")
    if not isinstance(run_slug, str) or not _SLUG.fullmatch(run_slug):
        raise ValueError("run_slug must contain only letters, digits, underscore, or hyphen")
    source = template.get("run_canary")
    if not isinstance(source, str) or not source.startswith(RUN_CANARY_PREFIX):
        raise ValueError("workload template run canary is invalid")
    target = RUN_CANARY_PREFIX + run_slug
    value = deepcopy(dict(template))
    value["run_id"] = run_slug
    value["run_canary"] = target
    for turn in value.get("turns", []):
        if not isinstance(turn, dict) or turn.get("run_canary") != source or source not in turn.get("text", ""):
            raise ValueError("workload turn is not bound to the template run canary")
        turn["run_canary"] = target
        turn["text"] = turn["text"].replace(source, target)
    if source in str(value.get("turns")):
        raise ValueError("workload instantiation retained the template canary")
    return value, {RUN_CANARY_ENV: target}
