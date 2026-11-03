"""Перевод ошибок модуля проектов в HTTP-ответы."""
from __future__ import annotations

import functools
from typing import Any, Awaitable, Callable, Optional

from fastapi import HTTPException

from services.api_gateway.projects.errors import (
    DomainNotVerified,
    InsufficientRole,
    ProjectError,
    ProjectNotFound,
    RateLimited,
)


def to_http_exception(exc: ProjectError, *, not_found_detail: Optional[str] = None) -> HTTPException:
    if isinstance(exc, ProjectNotFound) and not_found_detail is not None:
        return HTTPException(status_code=404, detail=not_found_detail)
    detail: dict = {"error": exc.code, "message": exc.message}
    if isinstance(exc, DomainNotVerified):
        detail["reason"] = exc.reason
    headers = {"Retry-After": str(exc.retry_after)} if isinstance(exc, RateLimited) else None
    return HTTPException(status_code=exc.http_status, detail=detail, headers=headers)


def handle_project_errors(func: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
    @functools.wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return await func(*args, **kwargs)
        except ProjectError as exc:
            raise to_http_exception(exc) from exc

    return wrapper


__all__ = ["handle_project_errors", "to_http_exception", "InsufficientRole"]
