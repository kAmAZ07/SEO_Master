from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
import secrets
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
    SUPPORTED_PLATFORMS = ("tilda", "wordpress", "gsc", "ga4", "yandex")
    WORDPRESS_SECRET_BYTES = 32

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
        key_meta = self._build_hmac_key_metadata(hmac_secret)
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
                "status": "connected",
                "hmac_key": key_meta,
                "secret_delivery": "manual",
            },
        )
        return self.serialize_integration(integration)

    async def generate_wordpress_secret(
        self,
        db: Session,
        project_id: str,
        *,
        base_url: str,
    ) -> Dict[str, Any]:
        normalized_base_url = self._normalize_base_url(base_url)
        if not normalized_base_url:
            raise IntegrationValidationError("WordPress base_url is required")

        secret = self._generate_secret()
        key_meta = self._build_hmac_key_metadata(secret)
        details = {
            "base_url": normalized_base_url,
            "status": "secret_generated",
            "hmac_key": key_meta,
            "secret_delivery": "shown_once",
        }
        integration = self._upsert_integration(
            db=db,
            project_id=str(project_id),
            platform="wordpress",
            credentials={
                "base_url": normalized_base_url,
                "hmac_secret": secret,
            },
            hint_source=key_meta["fingerprint"],
            details=details,
        )
        return {
            **self.serialize_integration(integration),
            "generated_secret": secret,
            "wp_config_line": self._build_wp_config_line(secret),
        }

    async def rotate_wordpress_secret(
        self,
        db: Session,
        project_id: str,
    ) -> Dict[str, Any]:
        integration = self._require_integration(db, str(project_id), "wordpress")
        credentials = self.vault.decrypt(integration.encrypted_creds)
        details = dict(integration.details or {})
        base_url = credentials.get("base_url") or details.get("base_url")
        if not base_url:
            raise IntegrationValidationError("WordPress base_url is missing for this integration")

        previous_secret = credentials.get("hmac_secret")
        new_secret = self._generate_secret()
        key_meta = self._build_hmac_key_metadata(new_secret)
        rotation_meta: Dict[str, Any] = {
            "rotated_at": datetime.now(timezone.utc).isoformat(),
            "grace_until": key_meta["grace_until"],
        }
        if previous_secret:
            rotation_meta["previous_fingerprint"] = self._fingerprint_secret(previous_secret)

        updated_credentials = {
            "base_url": str(base_url),
            "hmac_secret": new_secret,
        }
        if previous_secret:
            updated_credentials["previous_hmac_secret"] = previous_secret

        details = {
            **details,
            "base_url": str(base_url),
            "status": "rotation_pending",
            "hmac_key": key_meta,
            "hmac_rotation": rotation_meta,
            "secret_delivery": "shown_once",
        }
        integration.encrypted_creds = self.vault.encrypt(updated_credentials)
        integration.creds_hint = self._build_hint(key_meta["fingerprint"])
        integration.details = details
        db.add(integration)
        db.commit()
        db.refresh(integration)

        return {
            **self.serialize_integration(integration),
            "generated_secret": new_secret,
            "wp_config_line": self._build_wp_config_line(new_secret),
        }

    async def verify_wordpress_integration(
        self,
        db: Session,
        project_id: str,
    ) -> Dict[str, Any]:
        integration = self._require_integration(db, str(project_id), "wordpress")
        credentials = self.vault.decrypt(integration.encrypted_creds)
        details = dict(integration.details or {})
        base_url = credentials.get("base_url") or details.get("base_url")
        if not base_url:
            raise IntegrationValidationError("WordPress base_url is missing for this integration")

        plugin_health = await self.validate_wordpress_plugin(str(base_url))
        details = {
            **details,
            "base_url": str(base_url),
            "status": "connected",
            "plugin_health": plugin_health,
            "verified_at": datetime.now(timezone.utc).isoformat(),
        }
        integration.details = details
        db.add(integration)
        db.commit()
        db.refresh(integration)
        return self.serialize_integration(integration)

    async def save_gsc_credentials(
        self,
        db: Session,
        project_id: str,
        *,
        property_url: str,
        credentials_json: str,
        token_json: str | None = None,
    ) -> Dict[str, Any]:
        normalized_property_url = self._normalize_gsc_property_url(property_url)
        credentials = self._parse_json_payload(credentials_json, "GSC credentials_json")
        token = self._parse_optional_json_payload(token_json, "GSC token_json")
        auth_mode = self._detect_google_auth_mode(credentials, token, platform="gsc")
        account_identifier = self._extract_google_account_identifier(credentials)

        integration = self._upsert_integration(
            db=db,
            project_id=str(project_id),
            platform="gsc",
            credentials={
                "property_url": normalized_property_url,
                "credentials": credentials,
                "token": token,
            },
            hint_source=normalized_property_url,
            details={
                "property_url": normalized_property_url,
                "auth_mode": auth_mode,
                "account_identifier": account_identifier,
            },
        )
        return self.serialize_integration(integration)

    async def save_ga4_credentials(
        self,
        db: Session,
        project_id: str,
        *,
        property_id: str,
        credentials_json: str,
        token_json: str | None = None,
    ) -> Dict[str, Any]:
        normalized_property_id = self._normalize_required_identifier(property_id, "GA4 property_id")
        credentials = self._parse_json_payload(credentials_json, "GA4 credentials_json")
        token = self._parse_optional_json_payload(token_json, "GA4 token_json")
        auth_mode = self._detect_google_auth_mode(credentials, token, platform="ga4")
        account_identifier = self._extract_google_account_identifier(credentials)

        integration = self._upsert_integration(
            db=db,
            project_id=str(project_id),
            platform="ga4",
            credentials={
                "property_id": normalized_property_id,
                "credentials": credentials,
                "token": token,
            },
            hint_source=normalized_property_id,
            details={
                "property_id": normalized_property_id,
                "auth_mode": auth_mode,
                "account_identifier": account_identifier,
            },
        )
        return self.serialize_integration(integration)

    async def save_yandex_credentials(
        self,
        db: Session,
        project_id: str,
        *,
        token: str,
        user_id: str,
        host_id: str,
    ) -> Dict[str, Any]:
        normalized_token = self._normalize_required_identifier(token, "Yandex token")
        normalized_user_id = self._normalize_required_identifier(user_id, "Yandex user_id")
        normalized_host_id = self._normalize_required_identifier(host_id, "Yandex host_id")

        integration = self._upsert_integration(
            db=db,
            project_id=str(project_id),
            platform="yandex",
            credentials={
                "token": normalized_token,
                "user_id": normalized_user_id,
                "host_id": normalized_host_id,
            },
            hint_source=normalized_host_id,
            details={
                "host_id": normalized_host_id,
                "user_id": normalized_user_id,
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

    def get_gsc_credentials(self, db: Session, project_id: str) -> Dict[str, Any]:
        integration = self._require_integration(db, str(project_id), "gsc")
        credentials = self.vault.decrypt(integration.encrypted_creds)
        details = dict(integration.details or {})
        return {
            "property_url": credentials.get("property_url") or details.get("property_url"),
            "credentials": dict(credentials.get("credentials") or {}),
            "token": credentials.get("token"),
            "auth_mode": details.get("auth_mode"),
            "account_identifier": details.get("account_identifier"),
        }

    def get_ga4_credentials(self, db: Session, project_id: str) -> Dict[str, Any]:
        integration = self._require_integration(db, str(project_id), "ga4")
        credentials = self.vault.decrypt(integration.encrypted_creds)
        details = dict(integration.details or {})
        return {
            "property_id": credentials.get("property_id") or details.get("property_id"),
            "credentials": dict(credentials.get("credentials") or {}),
            "token": credentials.get("token"),
            "auth_mode": details.get("auth_mode"),
            "account_identifier": details.get("account_identifier"),
        }

    def get_yandex_credentials(self, db: Session, project_id: str) -> Dict[str, Any]:
        integration = self._require_integration(db, str(project_id), "yandex")
        credentials = self.vault.decrypt(integration.encrypted_creds)
        details = dict(integration.details or {})
        return {
            "token": credentials["token"],
            "user_id": credentials.get("user_id") or details.get("user_id"),
            "host_id": credentials.get("host_id") or details.get("host_id"),
        }

    def get_credentials(self, db: Session, project_id: str, platform: str) -> Dict[str, Any]:
        normalized_platform = self._normalize_platform(platform)
        if normalized_platform == "tilda":
            return self.get_tilda_credentials(db, project_id)
        if normalized_platform == "wordpress":
            return self.get_wordpress_credentials(db, project_id)
        if normalized_platform == "gsc":
            return self.get_gsc_credentials(db, project_id)
        if normalized_platform == "ga4":
            return self.get_ga4_credentials(db, project_id)
        if normalized_platform == "yandex":
            return self.get_yandex_credentials(db, project_id)
        raise IntegrationValidationError(f"Unsupported platform: {platform}")

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
        payload["status"] = str(details.get("status") or payload["status"])
        if target_platform == "tilda":
            payload["project_identifier"] = details.get("external_project_id")
            payload["page_mappings_count"] = len(details.get("page_mappings") or {})
        elif target_platform == "wordpress":
            payload["site_url"] = details.get("base_url")
            payload["plugin_health"] = details.get("plugin_health") or {}
            hmac_key = details.get("hmac_key") if isinstance(details.get("hmac_key"), dict) else {}
            rotation = details.get("hmac_rotation") if isinstance(details.get("hmac_rotation"), dict) else {}
            payload["hmac_key_id"] = hmac_key.get("key_id")
            payload["hmac_secret_fingerprint"] = hmac_key.get("fingerprint")
            payload["hmac_secret_generated_at"] = hmac_key.get("generated_at")
            payload["hmac_secret_expires_at"] = hmac_key.get("expires_at")
            payload["hmac_secret_grace_until"] = hmac_key.get("grace_until") or rotation.get("grace_until")
            payload["hmac_rotation"] = rotation or None
        elif target_platform == "gsc":
            payload["site_url"] = details.get("property_url")
            payload["project_identifier"] = details.get("account_identifier")
            payload["auth_mode"] = details.get("auth_mode")
        elif target_platform == "ga4":
            payload["project_identifier"] = details.get("property_id")
            payload["account_identifier"] = details.get("account_identifier")
            payload["auth_mode"] = details.get("auth_mode")
        elif target_platform == "yandex":
            payload["project_identifier"] = details.get("host_id")
            payload["account_identifier"] = details.get("user_id")

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

    def _generate_secret(self) -> str:
        return secrets.token_urlsafe(self.WORDPRESS_SECRET_BYTES)

    def _fingerprint_secret(self, secret: str) -> str:
        return hashlib.sha256(secret.encode("utf-8")).hexdigest()

    def _build_hmac_key_metadata(self, secret: str) -> Dict[str, Any]:
        now = datetime.now(timezone.utc)
        rotation_days = int(os.getenv("HMAC_ROTATION_DAYS", "90"))
        grace_days = int(os.getenv("HMAC_GRACE_DAYS", "7"))
        fingerprint = self._fingerprint_secret(secret)
        return {
            "key_id": f"wp_{fingerprint[:16]}",
            "fingerprint": fingerprint[:16],
            "generated_at": now.isoformat(),
            "expires_at": (now + timedelta(days=rotation_days)).isoformat(),
            "grace_until": (now + timedelta(days=rotation_days + grace_days)).isoformat(),
            "rotation_days": rotation_days,
            "grace_days": grace_days,
        }

    def _build_wp_config_line(self, secret: str) -> str:
        escaped = secret.replace("\\", "\\\\").replace("'", "\\'")
        return f"define('SEO_MASTER_HMAC_SECRET', '{escaped}');"

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

    def _normalize_required_identifier(self, value: str, label: str) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise IntegrationValidationError(f"{label} is required")
        return normalized

    def _normalize_gsc_property_url(self, property_url: str) -> str:
        normalized = self._normalize_required_identifier(property_url, "GSC property_url")
        if normalized.startswith("sc-domain:"):
            domain = normalized.removeprefix("sc-domain:").strip()
            if not domain:
                raise IntegrationValidationError("GSC property_url must contain a domain after sc-domain:")
            return f"sc-domain:{domain}"

        parsed = urlparse(normalized)
        if not parsed.scheme or not parsed.netloc:
            raise IntegrationValidationError(
                "GSC property_url must be an absolute URL or an sc-domain: property identifier"
            )
        return normalized

    def _parse_json_payload(self, raw_json: str, label: str) -> Dict[str, Any]:
        text = str(raw_json or "").strip()
        if not text:
            raise IntegrationValidationError(f"{label} is required")
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise IntegrationValidationError(f"{label} must be valid JSON") from exc
        if not isinstance(payload, dict):
            raise IntegrationValidationError(f"{label} must be a JSON object")
        return payload

    def _parse_optional_json_payload(self, raw_json: str | None, label: str) -> Dict[str, Any] | None:
        text = str(raw_json or "").strip()
        if not text:
            return None
        return self._parse_json_payload(text, label)

    def _detect_google_auth_mode(
        self,
        credentials: Dict[str, Any],
        token: Dict[str, Any] | None,
        *,
        platform: str,
    ) -> str:
        credential_type = str(credentials.get("type") or "").strip().lower()
        if credential_type == "service_account":
            return "service_account"
        if credential_type == "authorized_user":
            return "authorized_user"
        if "installed" in credentials or "web" in credentials:
            if token is None:
                raise IntegrationValidationError(
                    f"{platform.upper()} token_json is required when credentials_json contains an OAuth client configuration"
                )
            return "oauth_client"
        if token is not None and (credentials.get("client_id") or credentials.get("client_secret")):
            return "oauth_client"
        raise IntegrationValidationError(
            f"{platform.upper()} credentials_json must be a Google service-account, authorized-user, or OAuth client payload"
        )

    def _extract_google_account_identifier(self, credentials: Dict[str, Any]) -> str | None:
        for key in ("client_email", "client_id", "quota_project_id"):
            value = credentials.get(key)
            if value:
                return str(value)
        installed = credentials.get("installed")
        if isinstance(installed, dict):
            for key in ("client_id", "project_id"):
                value = installed.get(key)
                if value:
                    return str(value)
        web = credentials.get("web")
        if isinstance(web, dict):
            for key in ("client_id", "project_id"):
                value = web.get(key)
                if value:
                    return str(value)
        return None
