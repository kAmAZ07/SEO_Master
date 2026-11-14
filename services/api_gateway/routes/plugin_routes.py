from pathlib import Path

from fastapi import APIRouter, HTTPException, Response, status

from services.api_gateway.config import settings
from shared.plugin_package import PackageError, read_package_file, trust_keys, verify_package


router = APIRouter(tags=["plugins"])

@router.get("/api/wordpress-plugin")
def download_wordpress_plugin() -> Response:
    # Public download, bounded and fail-closed. Pinning a digest also prevents
    # serving a different, otherwise valid signed historical artifact.
    try:
        if not settings.WORDPRESS_PLUGIN_PACKAGE_PATH or not settings.WORDPRESS_PLUGIN_PACKAGE_SHA256:
            raise PackageError("package_not_configured")
        keys = trust_keys(settings.WORDPRESS_PLUGIN_RELEASE_KEYS_JSON)
        data = read_package_file(Path(settings.WORDPRESS_PLUGIN_PACKAGE_PATH))
        verify_package(data, keys, expected_sha256=settings.WORDPRESS_PLUGIN_PACKAGE_SHA256)
    except (PackageError, OSError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="WordPress plugin package is not available",
        ) from None

    # Serve exactly the verified byte snapshot, never reopen a mutable path.
    return Response(
        content=data,
        media_type="application/zip",
        headers={
            "Cache-Control": "no-store",
            "Content-Disposition": 'attachment; filename="seo-master-connector.zip"',
            "X-Content-Type-Options": "nosniff",
        },
    )
