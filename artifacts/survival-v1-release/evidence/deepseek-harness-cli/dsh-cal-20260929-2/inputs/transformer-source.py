"""Fixed canonical-byte DSH privacy transformation; no scoring facts supplied."""
import copy
import json
import re

from .native_replay import canonical


def aliases_for_home(home):
    original = str(home)
    room = len(original.encode()) - len("/public/")
    if room < 1 or not original.isascii():
        raise ValueError("supported DSH path alias requires this capture's ASCII home path")
    replacement = "/public/" + ("user" + "x" * room)[:room]
    username = home.name
    return {original: replacement, original.lstrip("/").replace("/", "-"): replacement.lstrip("/").replace("/", "-"),
            username: ("user" + "x" * len(username))[:len(username)]}


VENDOR_MARKER = "[vendor instruction text removed at equal byte length]"


def _opaque(value, marker="[redacted unscored context]"):
    size = len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()) - 2
    return (marker + " " * size)[:size]


def sanitize(value, *, aliases):
    """Preserve every object/array/type and canonical byte count.

    Aliases affect personal runtime path/name literals consistently. Only text
    leaves in explicit system messages or runtime-context/skill-catalog messages
    are obscured. Required user messages, assistant explanations, tool commands,
    result payloads and persisted token values retain all facts; only literal
    personal paths/names inside them receive same-length aliases.

    Vendor instruction text is obscured too, with its own marker: every
    ``description`` string of a tool definition (``tools`` of a request
    header, schema descriptions included) and a ``system`` prompt string (the
    title request). Tool names, keys and types stay.
    """
    before = canonical(value)
    def walk(item, private_context=False, inherited_context=False, tools=False, vendor=False):
        if isinstance(item, str):
            if vendor:
                return _opaque(item, VENDOR_MARKER)
            if private_context:
                return _opaque(item)
            result = item
            for source, target in sorted(aliases.items(), key=lambda pair: -len(pair[0])):
                if len(source.encode()) != len(target.encode()):
                    raise ValueError("DSH alias changes UTF8 byte length")
                result = re.sub(r"\b" + re.escape(source) + r"\b", lambda _: target, result) if "/" not in source and "-" not in source else result.replace(source, target)
            return result
        if isinstance(item, list):
            return [walk(child, private_context, inherited_context, tools, vendor) for child in item]
        if isinstance(item, dict):
            source = item.get("source")
            context = inherited_context or item.get("role") == "system" or isinstance(source, dict) and source.get("kind") in {"runtime-context", "skill-catalog"}
            result = {}
            for key, child in item.items():
                # Preserve discriminants, native IDs, source kind, and field
                # structure. Obscure only these known unscored textual leaves.
                redact = context and key in {"text", "description", "name"}
                # Inside a tool definition list every description string is vendor text.
                inside = tools or (key == "tools" and isinstance(child, list))
                blank = (inside and key == "description" and isinstance(child, str)) or (key == "system" and isinstance(child, str))
                result[key] = walk(child, redact or private_context, context, inside, blank)
            return result
        return copy.deepcopy(item)
    derived = walk(value)
    if len(canonical(derived)) != len(before):
        raise ValueError("DSH transformation changed canonical logical bytes")
    return derived
