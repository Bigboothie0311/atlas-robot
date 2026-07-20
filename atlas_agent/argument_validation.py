from __future__ import annotations

import re
from typing import Any


WORKFLOW_REFERENCE_PATTERN = re.compile(
    r"^steps\.[1-9][0-9]*\.output"
    r"(?:\.[A-Za-z0-9_-]+)*$"
)


class ArgumentValidationError(ValueError):
    pass


def validate_tool_arguments(
    arguments: Any,
    schema: Any,
) -> None:
    """Validate tool arguments against Atlas's JSON-schema subset.

    Exact workflow references are allowed as deferred values because the
    workflow runner resolves them before calling the tool. Everything
    else must satisfy the registered tool schema.
    """

    if not isinstance(arguments, dict):
        raise ArgumentValidationError(
            "arguments must be an object"
        )

    if not isinstance(schema, dict):
        raise ArgumentValidationError(
            "tool parameter schema must be an object"
        )

    _validate_value(
        arguments,
        schema,
        path="arguments",
        allow_reference=False,
    )


def _validate_value(
    value: Any,
    schema: dict[str, Any],
    *,
    path: str,
    allow_reference: bool = True,
) -> None:
    if allow_reference and isinstance(value, dict):
        if "$ref" in value:
            _validate_reference(value, path)
            return

    expected = schema.get("type")

    if expected is not None:
        expected_types = (
            [expected]
            if isinstance(expected, str)
            else expected
        )

        if (
            not isinstance(expected_types, list)
            or not expected_types
            or not all(
                isinstance(item, str)
                for item in expected_types
            )
        ):
            raise ArgumentValidationError(
                f"{path} has an invalid schema type"
            )

        if not any(
            _matches_type(value, type_name)
            for type_name in expected_types
        ):
            expected_text = " or ".join(expected_types)
            actual_text = _value_type_name(value)
            raise ArgumentValidationError(
                f"{path} must be {expected_text}, "
                f"not {actual_text}"
            )

    enum = schema.get("enum")

    if enum is not None:
        if not isinstance(enum, list):
            raise ArgumentValidationError(
                f"{path} has an invalid enum schema"
            )

        if value not in enum:
            raise ArgumentValidationError(
                f"{path} must be one of {enum!r}"
            )

    if isinstance(value, dict):
        _validate_object(value, schema, path)
    elif isinstance(value, list):
        _validate_array(value, schema, path)
    elif isinstance(value, str):
        _validate_string(value, schema, path)
    elif (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
    ):
        _validate_number(value, schema, path)


def _validate_reference(
    value: dict[str, Any],
    path: str,
) -> None:
    if set(value) != {"$ref"}:
        raise ArgumentValidationError(
            f"{path} contains a malformed workflow reference"
        )

    reference = value.get("$ref")

    if (
        not isinstance(reference, str)
        or not WORKFLOW_REFERENCE_PATTERN.fullmatch(reference)
    ):
        raise ArgumentValidationError(
            f"{path} contains an invalid workflow reference"
        )


def _validate_object(
    value: dict[str, Any],
    schema: dict[str, Any],
    path: str,
) -> None:
    properties = schema.get("properties", {})

    if not isinstance(properties, dict):
        raise ArgumentValidationError(
            f"{path} has invalid object properties"
        )

    required = schema.get("required", [])

    if not isinstance(required, list) or not all(
        isinstance(item, str)
        for item in required
    ):
        raise ArgumentValidationError(
            f"{path} has an invalid required list"
        )

    missing = [
        name
        for name in required
        if name not in value
    ]

    if missing:
        raise ArgumentValidationError(
            f"{path} is missing required fields: "
            f"{sorted(missing)}"
        )

    additional = schema.get(
        "additionalProperties",
        True,
    )
    unknown = sorted(set(value) - set(properties))

    if unknown and additional is False:
        raise ArgumentValidationError(
            f"{path} contains unknown fields: {unknown}"
        )

    for name, item in value.items():
        item_path = f"{path}.{name}"

        if name in properties:
            property_schema = properties[name]

            if not isinstance(property_schema, dict):
                raise ArgumentValidationError(
                    f"{item_path} has an invalid schema"
                )

            _validate_value(
                item,
                property_schema,
                path=item_path,
            )
        elif isinstance(additional, dict):
            _validate_value(
                item,
                additional,
                path=item_path,
            )


def _validate_array(
    value: list[Any],
    schema: dict[str, Any],
    path: str,
) -> None:
    minimum = schema.get("minItems")
    maximum = schema.get("maxItems")

    if minimum is not None and len(value) < minimum:
        raise ArgumentValidationError(
            f"{path} must contain at least {minimum} items"
        )

    if maximum is not None and len(value) > maximum:
        raise ArgumentValidationError(
            f"{path} must contain at most {maximum} items"
        )

    item_schema = schema.get("items")

    if item_schema is None:
        return

    if not isinstance(item_schema, dict):
        raise ArgumentValidationError(
            f"{path} has an invalid item schema"
        )

    for index, item in enumerate(value):
        _validate_value(
            item,
            item_schema,
            path=f"{path}[{index}]",
        )


def _validate_string(
    value: str,
    schema: dict[str, Any],
    path: str,
) -> None:
    minimum = schema.get("minLength")
    maximum = schema.get("maxLength")
    pattern = schema.get("pattern")

    if minimum is not None and len(value) < minimum:
        raise ArgumentValidationError(
            f"{path} must contain at least "
            f"{minimum} characters"
        )

    if maximum is not None and len(value) > maximum:
        raise ArgumentValidationError(
            f"{path} must contain at most "
            f"{maximum} characters"
        )

    if pattern is not None:
        if not isinstance(pattern, str):
            raise ArgumentValidationError(
                f"{path} has an invalid pattern schema"
            )

        try:
            matches = re.search(pattern, value)
        except re.error as error:
            raise ArgumentValidationError(
                f"{path} has an invalid pattern schema"
            ) from error

        if matches is None:
            raise ArgumentValidationError(
                f"{path} does not match its required pattern"
            )


def _validate_number(
    value: int | float,
    schema: dict[str, Any],
    path: str,
) -> None:
    minimum = schema.get("minimum")
    maximum = schema.get("maximum")

    if minimum is not None and value < minimum:
        raise ArgumentValidationError(
            f"{path} must be at least {minimum}"
        )

    if maximum is not None and value > maximum:
        raise ArgumentValidationError(
            f"{path} must be at most {maximum}"
        )


def _matches_type(
    value: Any,
    type_name: str,
) -> bool:
    if type_name == "null":
        return value is None

    if type_name == "boolean":
        return isinstance(value, bool)

    if type_name == "integer":
        return (
            isinstance(value, int)
            and not isinstance(value, bool)
        )

    if type_name == "number":
        return (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
        )

    if type_name == "string":
        return isinstance(value, str)

    if type_name == "array":
        return isinstance(value, list)

    if type_name == "object":
        return isinstance(value, dict)

    raise ArgumentValidationError(
        f"unsupported schema type: {type_name}"
    )


def _value_type_name(value: Any) -> str:
    if value is None:
        return "null"

    if isinstance(value, bool):
        return "boolean"

    if isinstance(value, int):
        return "integer"

    if isinstance(value, float):
        return "number"

    if isinstance(value, str):
        return "string"

    if isinstance(value, list):
        return "array"

    if isinstance(value, dict):
        return "object"

    return type(value).__name__
