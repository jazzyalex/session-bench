"""Dependency-free validation of the closed JSON Schema subset shipped here.

These are JSON Schema 2020-12 documents. The runtime deliberately supports only
keywords used by these bundled schemas and fails closed on unsupported keywords.
It is not a general-purpose JSON Schema implementation.
"""
import json
import re
from pathlib import Path
from urllib.parse import urlsplit

SCHEMAS = Path(__file__).resolve().parent.parent / "schemas" / "v1"
KEYWORDS = {"$schema", "$id", "$defs", "$ref", "title", "description", "type", "properties", "required",
            "additionalProperties", "items", "enum", "const", "minimum",
            "minLength", "minItems", "pattern", "format", "anyOf"}


def _resolve_local_ref(root, reference):
    if not isinstance(reference, str) or not reference.startswith("#/"):
        raise ValueError(f"unsupported schema reference: {reference!r}")
    target = root
    for encoded in reference[2:].split("/"):
        key = encoded.replace("~1", "/").replace("~0", "~")
        if not isinstance(target, dict) or key not in target:
            raise ValueError(f"unresolved schema reference: {reference!r}")
        target = target[key]
    if not isinstance(target, dict):
        raise ValueError(f"schema reference is not an object: {reference!r}")
    return target


def validate(value, schema, path="$", _root=None):
    root = schema if _root is None else _root
    unsupported = set(schema) - KEYWORDS
    if unsupported:
        raise ValueError(f"unsupported schema keywords: {sorted(unsupported)}")
    if "$ref" in schema:
        validate(value, _resolve_local_ref(root, schema["$ref"]), path, root)
    if "anyOf" in schema:
        for option in schema["anyOf"]:
            try:
                validate(value, option, path, root)
                break
            except ValueError:
                pass
        else:
            raise ValueError(f"{path}: no allowed schema matches")
    types = {"object": lambda v: isinstance(v, dict),
             "array": lambda v: isinstance(v, list),
             "string": lambda v: isinstance(v, str),
             "integer": lambda v: type(v) is int,
             "number": lambda v: type(v) in (int, float),
             "boolean": lambda v: type(v) is bool,
             "null": lambda v: v is None}
    if "type" in schema:
        ts = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if not any(types[t](value) for t in ts):
            raise ValueError(f"{path}: expected {ts}")
    if "const" in schema and (type(value) is not type(schema["const"]) or value != schema["const"]):
        raise ValueError(f"{path}: expected {schema['const']!r}")
    if "enum" in schema and not any(type(value) is type(x) and value == x for x in schema["enum"]):
        raise ValueError(f"{path}: invalid value {value!r}")
    if isinstance(value, dict):
        props = schema.get("properties", {})
        missing = set(schema.get("required", [])) - set(value)
        extra = set(value) - set(props)
        if missing:
            raise ValueError(f"{path}: missing required fields {sorted(missing)}")
        if extra and schema.get("additionalProperties") is False:
            raise ValueError(f"{path}: unknown fields {sorted(extra)}")
        for key in value.keys() & props.keys():
            validate(value[key], props[key], f"{path}.{key}", root)
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            raise ValueError(f"{path}: too few items")
        if "items" in schema:
            for i, item in enumerate(value):
                validate(item, schema["items"], f"{path}[{i}]", root)
    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            raise ValueError(f"{path}: empty/short string")
        if "pattern" in schema and not re.fullmatch(schema["pattern"], value):
            raise ValueError(f"{path}: invalid pattern")
        if schema.get("format") == "uri":
            parsed = urlsplit(value)
            if not parsed.scheme or (parsed.scheme in ("http", "https") and not parsed.netloc):
                raise ValueError(f"{path}: invalid URI")
        elif "format" in schema:
            raise ValueError(f"unsupported schema format: {schema['format']!r}")
    if type(value) in (int, float) and value < schema.get("minimum", value):
        raise ValueError(f"{path}: below minimum")


def validate_named(value, name):
    validate(value, json.loads((SCHEMAS / f"{name}.schema.json").read_text()))
