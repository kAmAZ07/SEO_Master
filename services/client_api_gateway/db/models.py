"""Совместимые aliases; обычный импорт db package всё ещё загружает runtime."""

from services.client_api_gateway.models import (
    Base as Base,
    ClientKey as ClientKey,
    DeploymentLog as DeploymentLog,
)
