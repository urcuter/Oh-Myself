"""API error types."""

from __future__ import annotations


class OhMyApiError(RuntimeError):
    pass


class AuthenticationFailure(OhMyApiError):
    pass


class RateLimitFailure(OhMyApiError):
    pass


class RequestFailure(OhMyApiError):
    pass

