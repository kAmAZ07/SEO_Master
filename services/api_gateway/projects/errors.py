"""Типизированные ошибки модуля проектов. `code` стабилен и попадает в ответ API."""
from __future__ import annotations

from typing import Optional


class ProjectError(Exception):
    code = "project_error"
    http_status = 400

    def __init__(self, message: str = "", *, code: Optional[str] = None):
        super().__init__(message or self.code)
        self.message = message or self.code
        if code:
            self.code = code


class ProjectNotFound(ProjectError):
    """Объекта нет или он чужой: ответ одинаков, чтобы не раскрывать чужие идентификаторы."""
    code = "project_not_found"
    http_status = 404


class InsufficientRole(ProjectError):
    code = "insufficient_role"
    http_status = 403


class ScopeInvalid(ProjectError):
    code = "scope_invalid"
    http_status = 422


class DomainNotVerified(ProjectError):
    code = "domain_not_verified"
    http_status = 403

    def __init__(self, reason: str, message: str = ""):
        super().__init__(message or f"domain ownership is not confirmed ({reason})")
        self.reason = reason


class UrlOutOfScope(ProjectError):
    code = "url_out_of_scope"
    http_status = 422


class NoPendingChallenge(ProjectError):
    code = "no_pending_challenge"
    http_status = 409


class ChallengeConflict(ProjectError):
    code = "challenge_conflict"
    http_status = 409


class NothingToRevoke(ProjectError):
    code = "nothing_to_revoke"
    http_status = 409


class RateLimited(ProjectError):
    code = "rate_limited"
    http_status = 429

    def __init__(self, retry_after: int, message: str = "", *, code: Optional[str] = None):
        super().__init__(message or f"retry after {retry_after}s", code=code)
        self.retry_after = max(1, int(retry_after))
