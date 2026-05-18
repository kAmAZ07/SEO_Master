from pathlib import Path

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import FileResponse


router = APIRouter(tags=["plugins"])

REPO_ROOT = Path(__file__).resolve().parents[3]
WORDPRESS_PLUGIN_DIST = REPO_ROOT / "adapters" / "wordpress-plugin" / "dist"


def _latest_wordpress_plugin_zip() -> Path | None:
    candidates = sorted(
        WORDPRESS_PLUGIN_DIST.glob("seo-master-connector-*.zip"),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None


@router.get("/api/wordpress-plugin")
async def download_wordpress_plugin():
    plugin_zip = _latest_wordpress_plugin_zip()
    if plugin_zip is None or not plugin_zip.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="WordPress plugin package is not available",
        )

    return FileResponse(
        path=plugin_zip,
        filename="seo-master-connector.zip",
        media_type="application/zip",
        headers={
            "Cache-Control": "no-store",
        },
    )
