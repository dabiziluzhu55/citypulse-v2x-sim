"""Shared session errors for API and worker processes."""

from __future__ import annotations


class SessionError(RuntimeError):
    pass


class SessionBusyError(SessionError):
    pass


class UnknownSessionError(SessionError):
    pass


class RedisUnavailableError(SessionError):
    pass
