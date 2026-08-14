"""Small deterministic JSON Schema validator for committed Forge contracts.

The supported vocabulary is intentionally limited to the keywords used by the
repository's Draft 2020-12 schemas. Unsupported keywords fail closed.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any


class SchemaValidationError(ValueError):
    pass


SUPPORTED_KEYWORDS = {
    "$schema",
    "$id",
    "title",
    "description",
    "type",
    "const",
    "enum",
    "required",
    "properties",
    "additionalProperties",
    "items",
    "minItems",
    "maxItems",
    "minLength",
    "maxLength",
    "pattern",
    "format",
    "minimum",
    "maximum",
}


def _fail(path: str, message: str) -> None:
    raise SchemaValidationError(f"{path}: {message}")


def _matches_type(value: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "null":
        return value is None
    raise SchemaValidationError(f"schema declares unsupported type {expected!r}")


def _validate_datetime(value: str, path: str) -> None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SchemaValidationError(f"{path}: value is not an RFC 3339 date-time") from exc
    if parsed.tzinfo is None:
        _fail(path, "date-time must include a timezone")


def validate_instance(value: Any, schema: dict[str, Any], path: str = "$") -> None:
    unknown = set(schema) - SUPPORTED_KEYWORDS
    if unknown:
        _fail(path, f"schema uses unsupported keywords: {', '.join(sorted(unknown))}")

    expected_type = schema.get("type")
    if expected_type is not None:
        expected = [expected_type] if isinstance(expected_type, str) else expected_type
        if not isinstance(expected, list) or not all(isinstance(item, str) for item in expected):
            _fail(path, "schema type must be a string or array of strings")
        if not any(_matches_type(value, item) for item in expected):
            _fail(path, f"expected type {' or '.join(expected)}")

    if "const" in schema and value != schema["const"]:
        _fail(path, f"value must equal {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        _fail(path, f"value is not one of {schema['enum']!r}")

    if isinstance(value, dict):
        required = schema.get("required", [])
        missing = [name for name in required if name not in value]
        if missing:
            _fail(path, f"missing required properties: {', '.join(missing)}")
        properties = schema.get("properties", {})
        if not isinstance(properties, dict):
            _fail(path, "schema properties must be an object")
        if schema.get("additionalProperties") is False:
            extras = sorted(set(value) - set(properties))
            if extras:
                _fail(path, f"unexpected properties: {', '.join(extras)}")
        for name, child in properties.items():
            if name in value:
                validate_instance(value[name], child, f"{path}.{name}")

    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            _fail(path, f"array has fewer than {schema['minItems']} items")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            _fail(path, f"array has more than {schema['maxItems']} items")
        item_schema = schema.get("items")
        if item_schema is not None:
            for index, item in enumerate(value):
                validate_instance(item, item_schema, f"{path}[{index}]")

    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            _fail(path, f"string is shorter than {schema['minLength']} characters")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            _fail(path, f"string is longer than {schema['maxLength']} characters")
        if "pattern" in schema and re.search(schema["pattern"], value) is None:
            _fail(path, f"string does not match pattern {schema['pattern']!r}")
        if schema.get("format") == "date-time":
            _validate_datetime(value, path)
        elif "format" in schema:
            _fail(path, f"schema uses unsupported format {schema['format']!r}")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            _fail(path, f"number is below minimum {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            _fail(path, f"number is above maximum {schema['maximum']}")
