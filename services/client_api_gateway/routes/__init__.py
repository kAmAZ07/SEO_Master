from services.client_api_gateway.routes.patch_endpoints import router as patch_router
from services.client_api_gateway.routes.health import router as health_router
from services.client_api_gateway.routes.key_management import router as key_management_router

__all__ = ["patch_router", "health_router", "key_management_router"]
