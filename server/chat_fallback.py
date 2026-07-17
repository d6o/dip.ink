"""Ordered model fallback for OpenAI-compatible chat completions.

Lets extraction/distillation degrade through a ladder of models when the
primary rate-limits or errors (quota windows, provider outages), instead of
failing the batch. Configure with LLM_MODEL_LADDER (comma-separated).
"""
from __future__ import annotations

import logging
import os
from collections.abc import Awaitable, Callable, Sequence
from typing import TypeVar

DEFAULT_CHAT_MODEL_LADDER = ("gpt-4.1-mini",)

T = TypeVar("T")


def parse_model_ladder(value: str | None = None) -> tuple[str, ...]:
    """Parse a comma-separated ladder, preserving order and removing duplicates."""
    raw = value if value is not None else os.environ.get("LLM_MODEL_LADDER", "")
    candidates = raw.split(",") if raw.strip() else DEFAULT_CHAT_MODEL_LADDER
    models: list[str] = []
    for candidate in candidates:
        model = candidate.strip()
        if model and model not in models:
            models.append(model)
    if not models:
        raise ValueError("LLM_MODEL_LADDER must contain at least one model")
    return tuple(models)


def _exception_chain(error: BaseException):
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = current.__cause__ or current.__context__


def is_recoverable_provider_error(error: BaseException) -> bool:
    """Return true only for transient/provider failures suitable for fallback."""
    for item in _exception_chain(error):
        status = getattr(item, "status_code", None)
        if status == 429 or status == 408 or (isinstance(status, int) and status >= 500):
            return True
        if isinstance(status, int) and 400 <= status < 500:
            # Auth, malformed requests, unsupported parameters, and unknown
            # models are deterministic configuration/application failures.
            return False

        name = type(item).__name__.lower()
        if any(marker in name for marker in (
            "ratelimit", "timeout", "connection", "internalserver", "serviceunavailable"
        )):
            return True

        message = str(item).lower()
        if any(marker in message for marker in (
            "rate limit", "rate-limit", "quota", "insufficient balance",
            "model_cooldown", "cooling down", "timed out", "timeout",
            "connection reset", "connection refused", "connection error",
            "provider unavailable", "service unavailable", "overloaded",
        )):
            return True
    return False


def _safe_error_label(error: BaseException) -> str:
    """Describe an error without logging its message, request, prompt, or headers."""
    status = next(
        (getattr(item, "status_code", None) for item in _exception_chain(error)
         if getattr(item, "status_code", None) is not None),
        None,
    )
    label = type(error).__name__
    return f"{label}(status={status})" if status is not None else label


class OrderedModelFallback:
    """Run calls through an ordered model ladder.

    With ``sticky=True``, once a later model succeeds this process starts future
    calls there. That avoids repeating known quota failures for every extraction
    prompt while preserving monotonic fallback to later models.
    """

    def __init__(
        self,
        models: Sequence[str] | None = None,
        *,
        context: str,
        logger: logging.Logger | None = None,
        sticky: bool = False,
    ) -> None:
        self.models = tuple(models or parse_model_ladder())
        if not self.models:
            raise ValueError("model fallback ladder cannot be empty")
        self.context = context
        self.logger = logger or logging.getLogger(__name__)
        self.sticky = sticky
        self._active_index = 0

    @property
    def active_model(self) -> str:
        return self.models[self._active_index]

    async def run(self, call: Callable[[str], Awaitable[T]]) -> T:
        start = self._active_index if self.sticky else 0
        for index in range(start, len(self.models)):
            model = self.models[index]
            try:
                result = await call(model)
            except Exception as error:
                if not is_recoverable_provider_error(error) or index + 1 >= len(self.models):
                    raise
                next_model = self.models[index + 1]
                self.logger.warning(
                    "chat model fallback context=%s from=%s to=%s error=%s",
                    self.context,
                    model,
                    next_model,
                    _safe_error_label(error),
                )
                continue

            if self.sticky and index > self._active_index:
                self._active_index = index
                self.logger.info(
                    "chat model selected context=%s model=%s",
                    self.context,
                    model,
                )
            return result

        raise RuntimeError("model fallback ladder exhausted without result")
