"""Агрегатор защищённых маршрутов API Gateway.

Файл раньше содержал все маршруты (1570 строк). Теперь маршруты лежат в пакете
`routes/protected/` по доменам. Порядок подключения совпадает с прежним порядком
объявления, поэтому сопоставление путей не изменилось.
"""
from fastapi import APIRouter

from services.api_gateway.routes.protected.auth_routes import router as auth_router
from services.api_gateway.routes.protected.dashboard_routes import router as dashboard_router
from services.api_gateway.routes.protected.projects_routes import router as projects_router
from services.api_gateway.routes.protected.audit_routes import router as audit_router
from services.api_gateway.routes.protected.hitl_routes import router as hitl_router
from services.api_gateway.routes.protected.backlinks_routes import router as backlinks_router
from services.api_gateway.routes.protected.content_routes import router as content_router
from services.api_gateway.routes.protected.keywords_routes import router as keywords_router

router = APIRouter()
for _sub in (
    auth_router,
    dashboard_router,
    projects_router,
    audit_router,
    hitl_router,
    backlinks_router,
    content_router,
    keywords_router,
):
    router.include_router(_sub)
