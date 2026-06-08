from __future__ import annotations

import dataclasses
import enum
import inspect
import types
from typing import Any, Callable, Literal, Union, get_args, get_origin, get_type_hints

_PYTHON_TYPE_TO_JSON: dict[type, str] = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    list: "array",
    dict: "object",
}

_JSON_TYPE_FOR_LITERAL: dict[type, str] = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
}


def _build_property_schema(py_type: type) -> dict[str, Any]:
    """Build a JSON Schema fragment for a single Python type annotation."""
    origin = get_origin(py_type)
    args = get_args(py_type)

    # Optional[X] / X | None  →  anyOf with null
    if origin is Union or isinstance(py_type, types.UnionType):
        non_none = [a for a in args if a is not type(None)]
        if len(non_none) == 1 and len(args) == 2:
            return {"anyOf": [_build_property_schema(non_none[0]), {"type": "null"}]}
        return {"anyOf": [_build_property_schema(a) for a in args]}

    # Literal["a", "b"] / Literal[1, 2]
    if origin is Literal:
        value_types = {type(v) for v in args}
        json_type = "string"
        if len(value_types) == 1:
            json_type = _JSON_TYPE_FOR_LITERAL.get(value_types.pop(), "string")
        return {"type": json_type, "enum": list(args)}

    # Enum subclass
    if isinstance(py_type, type) and issubclass(py_type, enum.Enum):
        values = [e.value for e in py_type]
        value_types = {type(v) for v in values}
        json_type = "string"
        if len(value_types) == 1:
            json_type = _JSON_TYPE_FOR_LITERAL.get(value_types.pop(), "string")
        return {"type": json_type, "enum": values}

    # Dataclass  →  nested object schema
    if isinstance(py_type, type) and dataclasses.is_dataclass(py_type):
        from edge_agent.schema import schema_from_dataclass
        return schema_from_dataclass(py_type)

    # list[X]  →  array with typed items
    if origin is list and args:
        return {"type": "array", "items": _build_property_schema(args[0])}

    # Bare primitive / fallback
    base = origin if origin is not None else py_type
    return {"type": _PYTHON_TYPE_TO_JSON.get(base, "string")}


class Tool:
    """Wraps a plain function with its JSON-schema metadata so providers can
    advertise it to the LLM and the agent loop can execute it."""

    __slots__ = ("fn", "name", "description", "parameters")

    def __init__(
        self,
        fn: Callable[..., Any],
        name: str,
        description: str,
        parameters: dict[str, Any],
    ) -> None:
        self.fn = fn
        self.name = name
        self.description = description
        self.parameters = parameters

    def __call__(self, **kwargs: Any) -> Any:
        return self.fn(**kwargs)

    def __repr__(self) -> str:
        return f"Tool(name={self.name!r})"


def _build_parameters_schema(fn: Callable[..., Any]) -> dict[str, Any]:
    sig = inspect.signature(fn)
    hints = get_type_hints(fn)
    hints.pop("return", None)

    properties: dict[str, Any] = {}
    required: list[str] = []

    for param_name, param in sig.parameters.items():
        py_type = hints.get(param_name, str)
        properties[param_name] = _build_property_schema(py_type)

        if param.default is inspect.Parameter.empty:
            required.append(param_name)

    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
    }
    if required:
        schema["required"] = required
    return schema


def tool(fn: Callable[..., Any]) -> Tool:
    """Decorator that turns a typed function into a :class:`Tool`.

    The function's name, docstring, and type hints are used to build the
    JSON-schema description that LLM providers need for function calling.
    """
    name = fn.__name__
    description = (fn.__doc__ or "").strip()
    parameters = _build_parameters_schema(fn)
    return Tool(fn=fn, name=name, description=description, parameters=parameters)
