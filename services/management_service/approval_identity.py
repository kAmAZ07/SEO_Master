"""Forward the actual access token to the existing session authority, never body user_id."""

import hmac

from fastapi import Depends, Header, HTTPException
import httpx

from services.management_service.config import settings
from services.management_service.approval_policy import UserActor
from services.semantic_service.proposals.model import UUID_RE


def require_service_identity(x_internal_api_key: str | None = Header(default=None)) -> None:
    key = settings.INTERNAL_API_KEY
    if not key or len(key) < 32:
        raise HTTPException(503, "service_identity_unconfigured")
    if x_internal_api_key is None or not hmac.compare_digest(x_internal_api_key.encode(), key.encode()):
        raise HTTPException(401, "service_identity_required")


async def authenticated_actor(
    authorization: str | None = Header(default=None),
    _: None = Depends(require_service_identity),
) -> UserActor:
    if (not authorization or not authorization.lower().startswith("bearer ")
            or len(authorization) > 8192 or not authorization[7:].strip()):
        raise HTTPException(401, "user_session_required")
    try:
        async with httpx.AsyncClient(timeout=5, trust_env=False, follow_redirects=False) as client:
            response = await client.get(
                settings.API_GATEWAY_URL.rstrip("/") + "/api/auth/me",
                headers={"Authorization": authorization},
            )
    except httpx.RequestError:
        raise HTTPException(503, "session_authority_unavailable") from None
    if response.status_code in (401, 403):
        raise HTTPException(401, "user_session_invalid")
    if response.status_code != 200 or len(response.content) > 16384:
        raise HTTPException(503, "session_authority_unavailable")
    try:
        data = response.json()
    except ValueError:
        raise HTTPException(503, "session_authority_invalid") from None
    actor_id = data.get("id") if isinstance(data, dict) else None
    if not isinstance(actor_id, str) or not UUID_RE.fullmatch(actor_id):
        raise HTTPException(503, "session_authority_invalid")
    return UserActor(actor_id)
