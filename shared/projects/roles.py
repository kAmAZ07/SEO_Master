"""Роли и права в workspace (ТЗ REQ-CORE §3.3).

Минимальная матрица для RC. В личном workspace владелец совмещает все роли. `user_id` из
тела запроса источником идентичности не является: роль определяется только членством.
"""
from __future__ import annotations

import enum


class Role(str, enum.Enum):
    OWNER = "owner"
    ADMIN = "admin"
    REVIEWER = "reviewer"
    DEPLOYER = "deployer"
    VIEWER = "viewer"


class Permission(str, enum.Enum):
    READ = "read"
    MANAGE = "manage"  # проект, источники, права, подтверждение домена
    APPROVE = "approve"
    DEPLOY = "deploy"


_MATRIX: dict[Role, frozenset[Permission]] = {
    Role.OWNER: frozenset(Permission),
    Role.ADMIN: frozenset(Permission),
    Role.REVIEWER: frozenset({Permission.READ, Permission.APPROVE}),
    Role.DEPLOYER: frozenset({Permission.READ, Permission.DEPLOY}),
    Role.VIEWER: frozenset({Permission.READ}),
}


def parse_role(value: str) -> Role:
    """Неизвестная роль — ошибка, а не молчаливый отказ или доступ."""
    try:
        return Role(value)
    except ValueError as exc:
        raise ValueError(f"unknown role: {value!r}") from exc


def has_permission(role: str, permission: Permission) -> bool:
    try:
        return permission in _MATRIX[parse_role(role)]
    except ValueError:
        return False  # запись с неизвестной ролью не даёт никаких прав
