from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional
from urllib.parse import urlparse

import httpx
from sqlalchemy.orm import Session

from services.project_integrations.credentials_vault import CredentialsVault
from services.project_integrations.models import ProjectIntegration


class IntegrationValidationError(ValueError):
    pass


class IntegrationNotFoundError(LookupError):
    pass


class IntegrationsService:
    SUPPORTED_PLATFORMS = ("tilda", "wordpress")

    def __init__(self, vault: CredentialsVault | None = None) -> None:
        self._vault = vault

    @property
    def vault(self) -> CredentialsVault:
        if self._vault is None:
            self._vault = CredentialsVault()
        return self._vault

    def list_integrations(self, db: Session, project_id: str) -> list[Dict[str, Any]]:
        rows = (
            db.query(ProjectIntegration)
            .filter(ProjectIntegration.project_id == str(project_id))
            .all()
        )
        by_platform = {row.platform: row for row in rows}
        return [self.serialize_integration(by_platform.get(platform), platform) for platform in self.SUPPORTED_PLATFORMS]

    async def save_tilda_credentials(
        self,
        db: Session,
        project_id: str,
        *,
        public_key: str,
        secret_key: str,
        external_project_id: str,
        page_mappings: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        public_key = (public_key or "").strip()
        secret_key = (secret_key or "").strip()
        external_project_id = str(external_project_id or "").strip()

        if not public_key or not secret_key or not external_project_id:
            raise IntegrationValidationError("Tilda public_key, secret_key, and project_id are required")

        integration = self._upsert_integration(
            db=db,
            project_id=str(project_id),
            platform="tilda",
            credentials={
                "public_key": public_key,
                "secret_key": secret_key,
                "project_id": external_project_id,
            },
            hint_source=public_key,
            details={
                "external_project_id": external_project_id,
                "page_mappings": dict(page_mappings or {}),
            },
        )
        return self.serialize_integration(integration)

    async def save_wordpress_credentials(
        self,
        db: Session,
        project_id: str,
        *,
        base_url: str,
        hmac_secret: str,
    ) -> Dict[str, Any]:
        normalized_base_url = self._normalize_base_url(base_url)
        hmac_secret = (hmac_secret or "").strip()
        if not normalized_base_url or not hmac_secret:
            raise IntegrationValidationError("WordPress base_url and hmac_secret are required")

        plugin_health = await self.validate_wordpress_plugin(normalized_base_url)
        integration = self._upsert_integration(
            db=db,
            project_id=str(project_id),
            platform="wordpress",
            credentials={
                "base_url": normalized_base_url,
                "hmac_secret": hmac_secret,
            },
            hint_source=hmac_secret,
            details={
                "base_url": normalized_base_url,
                "plugin_health": plugin_health,
            },
        )
        return self.serialize_integration(integration)

    def get_tilda_credentials(self, db: Session, project_id: str) -> Dict[str, Any]:
        integration = self._require_integration(db, str(project_id), "tilda")
        credentials = self.vault.decrypt(integration.encrypted_creds)
        details = dict(integration.details or {})
        return {
            "public_key": credentials["public_key"],
            "secret_key": credentials["secret_key"],
            "project_id": credentials.get("project_id") or details.get("external_project_id"),
            "page_mappings": dict(details.get("page_mappings") or {}),
        }

    def get_wordpress_credentials(self, db: Session, project_id: str) -> Dict[str, Any]:
        integration = self._require_integration(db, str(project_id), "wordpress")
        credentials = self.vault.decrypt(integration.encrypted_creds)
        details = dict(integration.details or {})
        return {
            "base_url": credentials["base_url"],
            "hmac_secret": credentials["hmac_secret"],
            "plugin_health": details.get("plugin_health") or {},
        }

    def revoke_integration(self, db: Session, project_id: str, platform: str) -> None:
        integration = self._require_integration(db, str(project_id), platform)
        db.delete(integration)
        db.commit()

    def resolve_tilda_page_id(
        self,
        db: Session,
        project_id: str,
        entity_id: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        credentials = self.get_tilda_credentials(db, str(project_id))
        metadata = metadata or {}
        page_mappings = credentials.get("page_mappings") or {}

        direct_candidates = [
            metadata.get("page_id"),
            metadata.get("tilda_page_id"),
            metadata.get("external_page_id"),
            metadata.get("pageId"),
        ]
        for candidate in direct_candidates:
            if candidate:
                return str(candidate)

        if entity_id in page_mappings:
            return str(page_mappings[entity_id])

        canonical_url = self._normalize_mapping_key(metadata.get("url") or entity_id)
        if canonical_url and canonical_url in page_mappings:
            return str(page_mappings[canonical_url])

        if self._looks_like_external_identifier(entity_id):
            return str(entity_id)

        raise IntegrationValidationError(
            "Tilda page mapping is missing for this entity. Provide page_id metadata or register a page mapping first."
        )

    def register_tilda_page_mapping(
        self,
        db: Session,
        *,
        external_project_id: str,
        page_id: str,
        page_url: str | None = None,
        event: str | None = None,
        payload: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        integration = (
            db.query(ProjectIntegration)
            .filter(
                ProjectIntegration.platform == "tilda",
                ProjectIntegration.details["external_project_id"].astext == str(external_project_id),
            )
            .one_or_none()
        )
        if integration is None:
            raise IntegrationNotFoundError(f"Tilda integration for external project {external_project_id} was not found")

        details = dict(integration.details or {})
        page_mappings = dict(details.get("page_mappings") or {})
        normalized_url = self._normalize_mapping_key(page_url)
        if normalized_url:
            page_mappings[normalized_url] = str(page_id)
        page_mappings[str(page_id)] = str(page_id)

        details["external_project_id"] = str(external_project_id)
        details["page_mappings"] = page_mappings
        details["last_webhook"] = {
            "page_id": str(page_id),
            "page_url": page_url,
            "event": event or "publish",
            "received_at": datetime.utcnow().isoformat(),
            "payload": payload or {},
        }
        integration.details = details
        db.add(integration)
        db.commit()
        db.refresh(integration)
        return {
            "project_id": integration.project_id,
            "platform": integration.platform,
            "page_id": str(page_id),
            "page_url": page_url,
            "external_project_id": str(external_project_id),
        }

    async def validate_wordpress_plugin(self, base_url: str) -> Dict[str, Any]:
        health_url = f"{base_url.rstrip('/')}/wp-json/seo-master/v1/health"
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            try:
                response = await client.get(health_url)
                response.raise_for_status()
            except httpx.HTTPError as exc:
                raise IntegrationValidationError(
                    f"WordPress plugin health check failed for {health_url}: {exc}"
                ) from exc

        data = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
        plugin_name = str(data.get("plugin") or "").strip().lower()
        status_value = str(data.get("status") or "").strip().lower()
        if plugin_name != "seo-master-connector" and status_value not in {"ok", "ready", "healthy"}:
            raise IntegrationValidationError("WordPress plugin health endpoint returned an unexpected response")
        return {
            "health_url": health_url,
            "status": status_value or "ok",
            "plugin": data.get("plugin") or "seo-master-connector",
            "version": data.get("version"),
        }

    def serialize_integration(
        self,
        integration: ProjectIntegration | None,
        platform: str | None = None,
    ) -> Dict[str, Any]:
        target_platform = (platform or (integration.platform if integration else "")).lower()
        payload: Dict[str, Any] = {
            "platform": target_platform,
            "connected": integration is not None,
            "status": "connected" if integration else "not_configured",
            "hint": integration.creds_hint if integration else None,
            "connected_at": integration.connected_at.isoformat() if integration and integration.connected_at else None,
            "updated_at": integration.updated_at.isoformat() if integration and integration.updated_at else None,
        }

        if integration is None:
            return payload

        details = dict(integration.details or {})
        if target_platform == "tilda":
            payload["project_identifier"] = details.get("external_project_id")
            payload["page_mappings_count"] = len(details.get("page_mappings") or {})
        elif target_platform == "wordpress":
            payload["site_url"] = details.get("base_url")
            payload["plugin_health"] = details.get("plugin_health") or {}

        return payload

    def _upsert_integration(
        self,
        *,
        db: Session,
        project_id: str,
        platform: str,
        credentials: Dict[str, Any],
        hint_source: str,
        details: Optional[Dict[str, Any]] = None,
    ) -> ProjectIntegration:
        normalized_platform = self._normalize_platform(platform)
        integration = self._get_integration(db, project_id, normalized_platform)
        encrypted_creds = self.vault.encrypt(credentials)

        if integration is None:
            integration = ProjectIntegration(
                project_id=project_id,
                platform=normalized_platform,
                encrypted_creds=encrypted_creds,
                creds_hint=self._build_hint(hint_source),
                details=details or {},
            )
        else:
            integration.encrypted_creds = encrypted_creds
            integration.creds_hint = self._build_hint(hint_source)
            integration.details = details or {}

        db.add(integration)
        db.commit()
        db.refresh(integration)
        return integration

    def _require_integration(self, db: Session, project_id: str, platform: str) -> ProjectIntegration:
        integration = self._get_integration(db, project_id, platform)
        if integration is None:
            raise IntegrationNotFoundError(
                f"{self._normalize_platform(platform)} integration is not configured for project {project_id}"
            )
        return integration

    def _get_integration(self, db: Session, project_id: str, platform: str) -> ProjectIntegration | None:
        return (
            db.query(ProjectIntegration)
            .filter(
                ProjectIntegration.project_id == str(project_id),
                ProjectIntegration.platform == self._normalize_platform(platform),
            )
            .one_or_none()
        )

    def _normalize_platform(self, platform: str) -> str:
        normalized = str(platform or "").strip().lower()
        if normalized not in self.SUPPORTED_PLATFORMS:
            raise IntegrationValidationError(f"Unsupported platform: {platform}")
        return normalized

    def _build_hint(self, value: str) -> str:
        cleaned = str(value or "").strip()
        if not cleaned:
            return "***"
        prefix = cleaned[:6]
        return prefix if len(cleaned) <= 6 else f"{prefix}..."

    def _normalize_base_url(self, base_url: str) -> str:
        value = str(base_url or "").strip()
        if not value:
            return ""
        parsed = urlparse(value if "://" in value else f"https://{value}")
        if not parsed.scheme or not parsed.netloc:
            raise IntegrationValidationError("WordPress base_url must be a valid absolute URL")
        return f"{parsed.scheme}://{parsed.netloc}".rstrip("/")

    def _normalize_mapping_key(self, value: str | None) -> str | None:
        if not value:
            return None
        value = str(value).strip()
        if not value:
            return None
        if "://" not in value:
            return value
        parsed = urlparse(value)
        normalized_path = parsed.path.rstrip("/") or "/"
        return f"{parsed.scheme}://{parsed.netloc}{normalized_path}"

    def _looks_like_external_identifier(self, value: str) -> bool:
        text = str(value or "").strip()
        if not text:
            return False
        if "://" in text:
            return False
        return True
