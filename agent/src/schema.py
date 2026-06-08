"""Dataclass → JSON-schema derivation and JSON → dataclass parsing.

All public functions are strictly typed — no ``Any`` is used.
"""

from __future__ import annotations

import dataclasses
import json
from typing import TypeVar, get_args, get_origin, get_type_hints

T = TypeVar("T")

_PYTHON_TYPE_TO_JSON: dict[type, str] = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    list: "array",
    dict: "object",
}


def _json_type(py_type: type) -> str:
    origin = get_origin(py_type)
    if origin is not None:
        py_type = origin
    return _PYTHON_TYPE_TO_JSON.get(py_type, "string")


def _field_schema(field_type: type) -> dict[str, object]:
    """Build the JSON-schema fragment for a single field type."""
    if dataclasses.is_dataclass(field_type):
        return schema_from_dataclass(field_type)

    origin = get_origin(field_type)

    if origin is list:
        args = get_args(field_type)
        items_type = args[0] if args else str
        if dataclasses.is_dataclass(items_type):
            items_schema: dict[str, object] = schema_from_dataclass(items_type)
        else:
            items_schema = {"type": _json_type(items_type)}
        return {"type": "array", "items": items_schema}

    return {"type": _json_type(field_type)}


def schema_from_dataclass(cls: type) -> dict[str, object]:
    """Derive a Gemini-compatible JSON schema from a dataclass type.

    Raises :class:`TypeError` if *cls* is not a dataclass.
    """
    if not dataclasses.is_dataclass(cls):
        raise TypeError(f"{cls!r} is not a dataclass")

    hints = get_type_hints(cls)
    dc_fields = dataclasses.fields(cls)  # type: ignore[arg-type]

    properties: dict[str, dict[str, object]] = {}
    required: list[str] = []

    for field in dc_fields:
        field_type = hints[field.name]
        properties[field.name] = _field_schema(field_type)

        has_default = (
            field.default is not dataclasses.MISSING
            or field.default_factory is not dataclasses.MISSING  # type: ignore[misc]
        )
        if not has_default:
            required.append(field.name)

    schema: dict[str, object] = {
        "type": "object",
        "properties": properties,
    }
    if required:
        schema["required"] = required
    return schema


def parse_dataclass(cls: type[T], data: dict[str, object]) -> T:
    """Construct a *cls* instance from *data*, recursing into nested dataclasses."""
    if not dataclasses.is_dataclass(cls):
        raise TypeError(f"{cls!r} is not a dataclass")

    hints = get_type_hints(cls)
    kwargs: dict[str, object] = {}

    for field in dataclasses.fields(cls):  # type: ignore[arg-type]
        if field.name not in data:
            continue
        value = data[field.name]
        field_type = hints[field.name]

        if dataclasses.is_dataclass(field_type) and isinstance(value, dict):
            kwargs[field.name] = parse_dataclass(field_type, value)
        elif get_origin(field_type) is list:
            args = get_args(field_type)
            if args and dataclasses.is_dataclass(args[0]) and isinstance(value, list):
                item_cls = args[0]
                kwargs[field.name] = [
                    parse_dataclass(item_cls, item) if isinstance(item, dict) else item
                    for item in value
                ]
            else:
                kwargs[field.name] = value
        else:
            kwargs[field.name] = value

    return cls(**kwargs)  # type: ignore[return-value]


def parse_json_to_dataclass(cls: type[T], raw: str) -> T:
    """Parse a JSON string and construct a *cls* dataclass instance."""
    data: dict[str, object] = json.loads(raw or "{}")
    return parse_dataclass(cls, data)
