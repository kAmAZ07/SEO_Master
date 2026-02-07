from services.api_gateway.routes.health import router as health_router
from services.api_gateway.routes.public_routes import router as public_router
from services.api_gateway.routes.protected_routes import router as protected_router

__all__ = ["health_router", "public_router", "protected_router"]
