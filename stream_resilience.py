"""Failure metadata and retry policy for OpenAI Responses API streams."""


RETRYABLE_EXCEPTION_NAMES = frozenset({
    "APIConnectionError",
    "APITimeoutError",
    "InternalServerError",
    "RateLimitError",
})

NON_RETRYABLE_ERROR_CODES = frozenset({
    "content_filter",
    "invalid_prompt",
    "invalid_request_error",
    "max_output_tokens",
    "max_output_tokens_exceeded",
    "model_not_found",
})


def _usage_tokens(response):
    usage = getattr(response, "usage", None)
    return (
        int(getattr(usage, "input_tokens", 0) or 0),
        int(getattr(usage, "output_tokens", 0) or 0),
    )


class StreamResponseError(RuntimeError):
    """A stream ended without a usable ``response.completed`` event."""

    def __init__(
        self,
        message,
        *,
        event_type="stream_ended",
        code=None,
        partial_text="",
        input_tokens=0,
        output_tokens=0,
        retryable=True,
    ):
        super().__init__(message)
        self.event_type = event_type
        self.code = code
        self.partial_text = partial_text
        self.input_tokens = int(input_tokens or 0)
        self.output_tokens = int(output_tokens or 0)
        self.retryable = bool(retryable)


def from_terminal_event(event, partial_text=""):
    """Builds a useful exception from ``error``, failed, or incomplete."""
    event_type = str(getattr(event, "type", "stream_error"))

    if event_type == "error":
        code = getattr(event, "code", None)
        message = getattr(event, "message", None) or "Responses stream error"
        input_tokens = 0
        output_tokens = 0
    else:
        response = getattr(event, "response", None)
        error = getattr(response, "error", None)
        incomplete = getattr(response, "incomplete_details", None)
        code = (
            getattr(error, "code", None)
            or getattr(incomplete, "reason", None)
        )
        message = (
            getattr(error, "message", None)
            or (f"Response ended as {event_type}: {code}" if code else None)
            or f"Response ended as {event_type}"
        )
        input_tokens, output_tokens = _usage_tokens(response)

    return StreamResponseError(
        message,
        event_type=event_type,
        code=code,
        partial_text=partial_text,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        retryable=code not in NON_RETRYABLE_ERROR_CODES,
    )


def from_exception(error, partial_text=""):
    """Wraps SDK/transport exceptions while preserving retry intent."""
    if isinstance(error, StreamResponseError):
        if partial_text and not error.partial_text:
            error.partial_text = partial_text
        return error

    name = type(error).__name__
    missing_completion = (
        isinstance(error, RuntimeError)
        and "response.completed" in str(error)
    )

    return StreamResponseError(
        str(error) or name,
        event_type=name,
        partial_text=partial_text,
        retryable=missing_completion or name in RETRYABLE_EXCEPTION_NAMES,
    )


def should_retry(error, *, spoken_any, interrupted, retry_attempted):
    """Retries once, and only when doing so cannot repeat spoken content."""
    return bool(
        isinstance(error, StreamResponseError)
        and error.retryable
        and not spoken_any
        and not interrupted
        and not retry_attempted
    )
