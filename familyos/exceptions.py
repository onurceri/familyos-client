"""The errors the client raises.

``FamilyOSError`` is the base of all of them. Errors the server answered carry its ``code`` (see
``ErrorCode``), ``message`` (for people) and the HTTP ``status``; the transport errors have no status.
"""

from typing import Any

from .models import ErrorCode


class FamilyOSError(Exception):
    """Something went wrong talking to FamilyOS."""

    def __init__(
        self,
        message: str,
        *,
        code: ErrorCode | None = None,
        status: int | None = None,
        fields: list[dict[str, str]] | None = None,
        reason: str | None = None,
        capability: str | None = None,
        current_version: int | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.status = status
        self.fields = fields or []
        """With ``bad_request``: what is wrong, as ``{"path": …, "message": …}``."""
        self.reason = reason
        """With ``forbidden``: ``capability``, ``approval``, ``critical`` or ``on_behalf``."""
        self.capability = capability
        self.current_version = current_version
        """With ``precondition_failed``: the version the thing has now."""

    def __str__(self) -> str:
        return f"{self.code.value}: {self.message}" if self.code else self.message


class BadRequestError(FamilyOSError):
    """The request is wrong (400, or 422 for a reused idempotency key); see ``fields``."""


class AuthenticationError(FamilyOSError):
    """No valid key (401)."""


class ForbiddenError(FamilyOSError):
    """The key's role, or the member it acts for, may not (403); see ``reason``."""


class NotFoundError(FamilyOSError):
    """There is no such thing (404), or its feature is switched off (``code`` is ``feature_off``)."""


class ConflictError(FamilyOSError):
    """The thing is not in a state that allows it (409)."""


class PreconditionFailed(FamilyOSError):
    """It changed since the version named in ``If-Match`` (412); ``current_version`` is the one it has now."""


class CursorExpired(FamilyOSError):
    """The changes since that cursor are no longer kept (410); read everything again."""


class RateLimited(FamilyOSError):
    """Too many requests (429); wait ``retry_after`` seconds."""

    def __init__(self, message: str, *, retry_after: float | None = None, **kwargs: Any) -> None:
        super().__init__(message, **kwargs)
        self.retry_after = retry_after


class ConnectionError(FamilyOSError):  # noqa: A001
    """FamilyOS could not be reached."""


class TimeoutError(FamilyOSError):  # noqa: A001
    """FamilyOS did not answer in time."""
