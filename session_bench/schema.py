"""Dependency-free validation of the closed JSON Schema subset shipped here.

These are JSON Schema 2020-12 documents. The runtime deliberately supports only
keywords used by these bundled schemas and fails closed on unsupported keywords.
It is not a general-purpose JSON Schema implementation.
"""
import json
import re
from pathlib import Path

SCHEMAS = Path(__file__).resolve().parent.parent / "schemas" / "v1"
KEYWORDS = {"$schema", "title", "description", "type", "properties", "required",
            "additionalProperties", "items", "enum", "const", "minimum",
            "minLength", "minItems", "pattern", "anyOf"}


def validate(value, schema, path="$"):
    unsupported = set(schema) - KEYWORDS
    if unsupported:
        raise ValueError(f"unsupported schema keywords: {sorted(unsupported)}")
    if "anyOf" in schema:
        for option in schema["anyOf"]:
            try:
                validate(value, option, path)
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
            validate(value[key], props[key], f"{path}.{key}")
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            raise ValueError(f"{path}: too few items")
        if "items" in schema:
            for i, item in enumerate(value):
                validate(item, schema["items"], f"{path}[{i}]")
    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            raise ValueError(f"{path}: empty/short string")
        if "pattern" in schema and not re.fullmatch(schema["pattern"], value):
            raise ValueError(f"{path}: invalid pattern")
    if type(value) in (int, float) and value < schema.get("minimum", value):
        raise ValueError(f"{path}: below minimum")


def validate_named(value, name):
    validate(value, json.loads((SCHEMAS / f"{name}.schema.json").read_text()))
