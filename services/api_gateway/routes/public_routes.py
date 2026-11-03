import ipaddress
import re
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import httpx
import redis
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from shared.netpolicy import NetPolicy, NetPolicyError, is_public_ip, parse_target

from config.logging_config import get_logger
from services.api_gateway.config import settings, get_redis_config

logger = get_logger(__name__)
router = APIRouter(prefix="/api/public", tags=["Public"])

# Окно фиксированное: отсчёт от первого принятого запуска, шестой запуск получает 429.
RATE_LIMIT_SCRIPT = """
local current = redis.call('INCR', KEYS[1])
if current == 1 then
  redis.call('EXPIRE', KEYS[1], ARGV[1])
end
local ttl = redis.call('TTL', KEYS[1])
if ttl < 0 then
  redis.call('EXPIRE', KEYS[1], ARGV[1])
  ttl = tonumber(ARGV[1])
end
return {current, ttl}
"""

_UID_PATTERN = re.compile(r"^[0-9a-fA-F-]{8,64}$")
_UNAVAILABLE_RETRY_AFTER = "30"

# Этап -> доля выполненной работы. Внутри `crawl` доля растёт по числу обработанных страниц.
_STAGE_PROGRESS = {
    "queued": 0,
    "starting": 3,
    "precheck": 6,
    "robots": 10,
    "sitemap": 15,
    "crawl": 20,
    "analyze": 72,
    "links": 80,
    "psi": 90,
    "done": 100,
}
_CRAWL_RANGE = (20, 70)
_STAGE_MESSAGES = {
    "queued": "Аудит поставлен в очередь",
    "starting": "Подготовка к проверке",
    "precheck": "Проверка адреса",
    "robots": "Чтение robots.txt",
    "sitemap": "Проверка sitemap.xml",
    "crawl": "Обход страниц",
    "analyze": "Анализ метаданных и разметки",
    "links": "Проверка внутренних ссылок",
    "psi": "Запрос PageSpeed Insights",
}
_FINAL_MESSAGES = {
    "completed": "Аудит завершён",
    "partial": "Аудит завершён частично: покрытие ограничено, причины указаны в отчёте",
    "failed": "Аудит не выполнен",
    "cancelled": "Аудит отменён",
    "expired": "Срок хранения результата истёк",
}


def _build_redis_client() -> Optional[redis.Redis]:
    try:
        cfg = get_redis_config()
        if cfg["url"]:
            return redis.Redis.from_url(
                cfg["url"],
                decode_responses=True,
                socket_connect_timeout=1,
                socket_timeout=1,
            )

        return redis.Redis(
            host=cfg["host"],
            port=cfg["port"],
            password=cfg["password"],
            db=cfg["db"],
            decode_responses=True,
            socket_connect_timeout=1,
            socket_timeout=1,
        )
    except Exception as exc:
        logger.error("Redis client init failed; public audit rate-limit unavailable", extra={"error": str(exc)})
        return None


redis_client = _build_redis_client()


def _rate_limit_unavailable(exc: Exception | None = None) -> HTTPException:
    if exc:
        logger.error("Public audit rate-limit unavailable", extra={"error": str(exc)})
    return HTTPException(
        status_code=503,
        detail={
            "error": "rate_limit_unavailable",
            "message": "Public audit rate limiting is temporarily unavailable. Please try again later.",
        },
        headers={"Retry-After": _UNAVAILABLE_RETRY_AFTER},
    )


def _get_redis_client() -> redis.Redis:
    global redis_client
    if redis_client is None:
        redis_client = _build_redis_client()
    if redis_client is None:
        raise _rate_limit_unavailable()
    return redis_client


def _trusted_networks() -> list:
    networks = []
    for item in (settings.PUBLIC_TRUSTED_PROXIES or "").split(","):
        item = item.strip()
        if not item:
            continue
        try:
            networks.append(ipaddress.ip_network(item, strict=False))
        except ValueError:
            logger.error("Invalid PUBLIC_TRUSTED_PROXIES entry ignored", extra={"entry": item})
    return networks


def _parse_ip(value: str):
    try:
        return ipaddress.ip_address(value.strip().split("%", 1)[0])
    except ValueError:
        return None


def _client_ip(request: Request) -> str:
    """Адрес клиента для ограничения частоты.

    Заголовки X-Forwarded-For / X-Real-IP учитываются только если соединение пришло от
    доверенного прокси. Цепочка разбирается справа налево: первый недоверенный адрес — клиент,
    всё левее может быть подделано отправителем и игнорируется.
    """
    peer_raw = request.client.host if request.client else ""
    peer = _parse_ip(peer_raw)
    if peer is None:
        return peer_raw or "unknown"
    trusted = _trusted_networks()
    if not any(peer in network for network in trusted):
        return str(peer)

    for candidate in reversed([part for part in request.headers.get("x-forwarded-for", "").split(",") if part.strip()]):
        ip = _parse_ip(candidate)
        if ip is not None and not any(ip in network for network in trusted):
            return str(ip)
    real_ip = _parse_ip(request.headers.get("x-real-ip", ""))
    if real_ip is not None and not any(real_ip in network for network in trusted):
        return str(real_ip)
    return str(peer)


def _rate_limit_key(request: Request) -> str:
    ip = _parse_ip(_client_ip(request))
    if ip is not None and ip.version == 6:
        # Один пользователь IPv6 обычно владеет целой подсетью /64: считаем её одним клиентом.
        ip = ipaddress.ip_network(f"{ip}/64", strict=False).network_address
    return f"rate_limit:public_audit:{ip if ip is not None else _client_ip(request)}"


def _rate_limit_headers(limit: int, remaining: int, reset_in_seconds: int) -> Dict[str, str]:
    return {
        "X-RateLimit-Limit": str(limit),
        "X-RateLimit-Remaining": str(max(0, remaining)),
        "X-RateLimit-Reset": str(reset_in_seconds),
        "Retry-After": str(reset_in_seconds),
    }


class QuickAuditRequest(BaseModel):
    url: str = Field(..., description="Website URL to audit")
    email: Optional[str] = Field(None, description="Optional email for notifications")

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        """Синтаксическая проверка по единой сетевой политике. Адреса проверяются ещё раз в audit-service."""
        v = v.strip()
        try:
            target = parse_target(v, NetPolicy())
        except NetPolicyError as exc:
            raise ValueError(f"URL is not allowed ({exc.code})") from exc
        # Адреса-литералы можно отклонить сразу, без DNS; имена проверяются в audit-service при соединении.
        if _parse_ip(target.host) is not None and not is_public_ip(target.host):
            raise ValueError("URL is not allowed (address_blocked)")
        return v


class AuditStatusResponse(BaseModel):
    uid: str
    audit_id: Optional[str] = None
    root_url: Optional[str] = None
    url: Optional[str] = None
    status: str
    stage: Optional[str] = None
    progress: int = 0
    counters: Dict[str, int] = Field(default_factory=dict)
    message: str = ""
    results: Optional[Dict[str, Any]] = None
    created_at: str
    completed_at: Optional[str] = None
    expires_at: Optional[str] = None


def _progress_percent(status: str, stage: Optional[str], counters: Dict[str, Any]) -> int:
    """Доля выполнения по реальному этапу и счётчикам, а не по одному статусу."""
    if status in ("completed", "partial", "failed", "cancelled", "expired"):
        return 100
    if status == "queued" or not stage:
        return 0
    if stage == "crawl":
        done, total = int(counters.get("pages_done") or 0), int(counters.get("pages_max") or 0)
        low, high = _CRAWL_RANGE
        return low + (round((high - low) * min(done, total) / total) if total > 0 else 0)
    return _STAGE_PROGRESS.get(stage, 0)


def _status_message(status: str, stage: Optional[str]) -> str:
    if status in _FINAL_MESSAGES:
        return _FINAL_MESSAGES[status]
    if status == "queued":
        return _STAGE_MESSAGES["queued"]
    return _STAGE_MESSAGES.get(stage or "", "Аудит выполняется")


async def check_rate_limit(request: Request) -> Dict[str, int]:
    client = _get_redis_client()
    key = _rate_limit_key(request)
    limit = settings.PUBLIC_RATE_LIMIT
    window = settings.PUBLIC_RATE_LIMIT_WINDOW_SECONDS

    try:
        current_count, ttl = client.eval(RATE_LIMIT_SCRIPT, 1, key, window)
    except Exception as exc:
        raise _rate_limit_unavailable(exc) from exc

    current = int(current_count or 0)
    reset_in_seconds = int(ttl or window)
    if reset_in_seconds <= 0:
        reset_in_seconds = window

    remaining = max(0, limit - current)
    if current > limit:
        raise HTTPException(
            status_code=429,
            detail={
                "error": "Rate limit exceeded",
                "limit": limit,
                "remaining": 0,
                "reset_in_seconds": reset_in_seconds,
                "window_seconds": window,
                "message": f"Maximum {limit} audits per hour.",
            },
            headers=_rate_limit_headers(limit, 0, reset_in_seconds),
        )

    return {
        "limit": limit,
        "remaining": remaining,
        "reset_in_seconds": reset_in_seconds,
        "window_seconds": window,
    }


def _refund_rate_limit(request: Request) -> None:
    """Запуск не принят из-за сбоя нашей стороны: возвращаем единицу лимита (best effort)."""
    try:
        _get_redis_client().decr(_rate_limit_key(request))
    except Exception as exc:  # noqa: BLE001
        logger.warning("Rate-limit refund failed", extra={"error_type": type(exc).__name__})


@router.post("/quick-audit", response_model=Dict[str, Any])
async def create_quick_audit(audit_request: QuickAuditRequest, request: Request):
    # Лимит списывается только для запросов, прошедших проверку тела: невалидный URL запуском не считается.
    rate_limit = await check_rate_limit(request)
    try:
        # Лимиты страниц и глубины заданы audit-service; клиентские опции не передаются.
        payload = {"root_url": audit_request.url, "site_type_hint": "unknown", "platform": "generic"}

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(f"{settings.AUDIT_SERVICE_URL}/audit/public", json=payload)
            response.raise_for_status()
            result = response.json()

        audit_uid = result.get("audit_id")
        if not audit_uid:
            raise HTTPException(status_code=502, detail="Audit service returned invalid response")

        return {
            "success": True,
            "uid": audit_uid,
            "audit_id": audit_uid,
            "status": "queued",
            "message": _STAGE_MESSAGES["queued"],
            "status_url": f"/api/public/audit-status/{audit_uid}",
            "estimated_time_seconds": settings.PUBLIC_AUDIT_TIMEOUT_SECONDS,
            "expires_in_days": settings.PUBLIC_AUDIT_RETENTION_DAYS,
            "rate_limit": rate_limit,
        }

    except HTTPException:
        _refund_rate_limit(request)
        raise
    except httpx.HTTPStatusError as exc:
        _refund_rate_limit(request)
        logger.error("Audit service error", extra={"status_code": exc.response.status_code})
        status_code = 400 if exc.response.status_code in (400, 422) else 502
        raise HTTPException(status_code=status_code, detail="Failed to start audit") from exc
    except httpx.RequestError as exc:
        _refund_rate_limit(request)
        logger.error("Failed to connect to Audit Service", extra={"error_type": type(exc).__name__})
        raise HTTPException(status_code=503, detail="Audit service temporarily unavailable", headers={"Retry-After": _UNAVAILABLE_RETRY_AFTER}) from exc


def _check_uid(uid: str) -> None:
    if not _UID_PATTERN.match(uid or ""):
        raise HTTPException(status_code=404, detail="Audit not found or expired")


def _map_audit_service_error(response: httpx.Response) -> None:
    if response.status_code == 404:
        raise HTTPException(status_code=404, detail="Audit not found or expired")
    if response.status_code == 410:
        raise HTTPException(status_code=410, detail={"error": "audit_expired", "message": "Audit result is no longer stored"})
    response.raise_for_status()


def _to_status_response(uid: str, view: Dict[str, Any]) -> AuditStatusResponse:
    status_value = view.get("status", "unknown")
    stage = view.get("stage")
    counters = {k: int(v) for k, v in (view.get("progress") or {}).items() if isinstance(v, (int, float))}
    final = status_value in ("completed", "partial", "failed", "cancelled", "expired")
    created_at = view.get("created_at") or datetime.now(timezone.utc).isoformat()
    top_findings = view.get("top_findings") or []
    summary = view.get("summary") or {}
    results = None
    if final or status_value in ("completed", "partial"):
        results = {
            "summary": summary,
            "findings": top_findings,
            "top_findings": top_findings,
            "findings_count": int(view.get("findings_count") or 0),
            "pages": view.get("pages") or [],
            "checks": summary.get("checks", {}),
            "partial_reasons": summary.get("partial_reasons", []),
            "error_code": summary.get("error_code"),
        }
    return AuditStatusResponse(
        uid=uid,
        audit_id=uid,
        root_url=view.get("root_url"),
        url=view.get("root_url"),
        status=status_value,
        stage=stage,
        progress=_progress_percent(status_value, stage, counters),
        counters=counters,
        message=_status_message(status_value, stage),
        results=results,
        created_at=created_at,
        completed_at=view.get("updated_at") if final else None,
        expires_at=view.get("expires_at"),
    )


@router.get("/audit-status/{uid}", response_model=AuditStatusResponse)
async def get_audit_status(uid: str):
    _check_uid(uid)
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(f"{settings.AUDIT_SERVICE_URL}/public/audit/{uid}")
        _map_audit_service_error(response)
        return _to_status_response(uid, response.json())
    except httpx.HTTPStatusError as exc:
        logger.error("Audit service error", extra={"status_code": exc.response.status_code})
        raise HTTPException(status_code=502, detail="Audit service error") from exc
    except httpx.RequestError as exc:
        logger.error("Failed to connect to Audit Service", extra={"error_type": type(exc).__name__})
        raise HTTPException(status_code=503, detail="Audit service temporarily unavailable", headers={"Retry-After": "5"}) from exc


@router.post("/audit/{uid}/cancel", response_model=AuditStatusResponse)
async def cancel_audit(uid: str):
    _check_uid(uid)
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(f"{settings.AUDIT_SERVICE_URL}/public/audit/{uid}/cancel")
        _map_audit_service_error(response)
        return _to_status_response(uid, response.json())
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=502, detail="Audit service error") from exc
    except httpx.RequestError as exc:
        raise HTTPException(status_code=503, detail="Audit service temporarily unavailable", headers={"Retry-After": "5"}) from exc


@router.get("/rate-limit-info")
async def get_rate_limit_info(request: Request):
    client = _get_redis_client()
    key = _rate_limit_key(request)

    try:
        current_count = client.get(key)
        remaining = settings.PUBLIC_RATE_LIMIT - (int(current_count) if current_count else 0)
        ttl = client.ttl(key)
    except Exception as exc:
        raise _rate_limit_unavailable(exc) from exc

    return {
        "limit": settings.PUBLIC_RATE_LIMIT,
        "remaining": max(0, remaining),
        "reset_in_seconds": ttl if ttl and ttl > 0 else settings.PUBLIC_RATE_LIMIT_WINDOW_SECONDS,
        "window_seconds": settings.PUBLIC_RATE_LIMIT_WINDOW_SECONDS,
        "mode": "redis",
    }
