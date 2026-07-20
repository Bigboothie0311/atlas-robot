import pytest

from atlas_agent.argument_validation import (
    ArgumentValidationError,
    validate_tool_arguments,
)


SEARCH_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "maxLength": 200,
        },
        "extensions": {
            "type": ["array", "null"],
            "items": {
                "type": "string",
            },
        },
        "limit": {
            "type": "integer",
            "minimum": 1,
            "maximum": 200,
        },
    },
    "required": [
        "query",
        "extensions",
        "limit",
    ],
    "additionalProperties": False,
}


DOWNLOAD_SCHEMA = {
    "type": "object",
    "properties": {
        "remote_path": {
            "type": "string",
        },
        "local_name": {
            "type": ["string", "null"],
        },
    },
    "required": [
        "remote_path",
        "local_name",
    ],
    "additionalProperties": False,
}


def test_accepts_valid_search_arguments():
    validate_tool_arguments(
        {
            "query": "Atlas",
            "extensions": None,
            "limit": 20,
        },
        SEARCH_SCHEMA,
    )


def test_rejects_unknown_sort_argument_from_live_plan():
    with pytest.raises(
        ArgumentValidationError,
        match="unknown fields.*sort",
    ):
        validate_tool_arguments(
            {
                "query": "Atlas",
                "extensions": None,
                "limit": 1,
                "sort": "modified_desc",
            },
            SEARCH_SCHEMA,
        )


def test_rejects_missing_required_fields():
    with pytest.raises(
        ArgumentValidationError,
        match="missing required fields.*extensions",
    ):
        validate_tool_arguments(
            {
                "query": "Atlas",
                "limit": 1,
            },
            SEARCH_SCHEMA,
        )


def test_rejects_wrong_download_argument_name():
    with pytest.raises(
        ArgumentValidationError,
        match="missing required fields.*remote_path",
    ):
        validate_tool_arguments(
            {
                "file_path": r"C:\Users\wesle\atlas.mp4",
                "local_name": None,
            },
            DOWNLOAD_SCHEMA,
        )


def test_accepts_deferred_workflow_reference():
    validate_tool_arguments(
        {
            "remote_path": {
                "$ref": "steps.2.output.0.path",
            },
            "local_name": None,
        },
        DOWNLOAD_SCHEMA,
    )


@pytest.mark.parametrize(
    "reference",
    [
        "",
        "step.2.output.0.path",
        "steps.0.output.0.path",
        "steps.two.output.path",
        "../secret",
    ],
)
def test_rejects_invalid_workflow_reference(reference):
    with pytest.raises(
        ArgumentValidationError,
        match="invalid workflow reference",
    ):
        validate_tool_arguments(
            {
                "remote_path": {
                    "$ref": reference,
                },
                "local_name": None,
            },
            DOWNLOAD_SCHEMA,
        )


def test_rejects_reference_with_extra_fields():
    with pytest.raises(
        ArgumentValidationError,
        match="malformed workflow reference",
    ):
        validate_tool_arguments(
            {
                "remote_path": {
                    "$ref": "steps.2.output.0.path",
                    "fallback": r"C:\unsafe.txt",
                },
                "local_name": None,
            },
            DOWNLOAD_SCHEMA,
        )


@pytest.mark.parametrize(
    ("limit", "message"),
    [
        (True, "must be integer"),
        (0, "must be at least 1"),
        (201, "must be at most 200"),
    ],
)
def test_rejects_invalid_integer_values(limit, message):
    with pytest.raises(
        ArgumentValidationError,
        match=message,
    ):
        validate_tool_arguments(
            {
                "query": "Atlas",
                "extensions": None,
                "limit": limit,
            },
            SEARCH_SCHEMA,
        )


def test_rejects_invalid_array_item_type():
    with pytest.raises(
        ArgumentValidationError,
        match=r"extensions\[1\] must be string",
    ):
        validate_tool_arguments(
            {
                "query": "Atlas",
                "extensions": ["mp4", 123],
                "limit": 20,
            },
            SEARCH_SCHEMA,
        )


def test_rejects_string_over_maximum_length():
    with pytest.raises(
        ArgumentValidationError,
        match="at most 200 characters",
    ):
        validate_tool_arguments(
            {
                "query": "x" * 201,
                "extensions": None,
                "limit": 20,
            },
            SEARCH_SCHEMA,
        )


def test_rejects_non_object_top_level_arguments():
    with pytest.raises(
        ArgumentValidationError,
        match="arguments must be an object",
    ):
        validate_tool_arguments(
            ["not", "an", "object"],
            SEARCH_SCHEMA,
        )
