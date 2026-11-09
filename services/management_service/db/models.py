"""Совместимые aliases; обычный импорт db package всё ещё загружает runtime."""

from services.management_service.models import (
    Base as Base,
    Task as Task,
    TaskType as TaskType,
    TaskStatus as TaskStatus,
    HITLStatus as HITLStatus,
    HITLApproval as HITLApproval,
    Changelog as Changelog,
    Project as Project,
    HITLHistory as HITLHistory,
    ManagementOutbox as ManagementOutbox,
)
