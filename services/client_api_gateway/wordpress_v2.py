"""The sole supported mutating CMS transport: exact WordPress v2 command bytes."""
from datetime import datetime, timezone
import hashlib
import hmac
import json
import re
import secrets
import time
from typing import Any

from shared.cms_http import CMSHttpClient
from shared.hitl_execution import ExecutionError, WordPressBinding, canonical, digest, snapshot, validate_receipt
from shared.projects.scope import parse_scope


def decode(body: bytes) -> dict[str, Any]:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise ExecutionError("cms_duplicate_json_key")
            value[key] = item
        return value
    try:
        value = json.loads(body, object_pairs_hook=unique, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError, UnicodeError):
        raise ExecutionError("cms_response_invalid") from None
    if not isinstance(value, dict):
        raise ExecutionError("cms_response_invalid")
    return value


def verify_signed(value: dict[str, Any], secret: str) -> None:
    signature = value.get("signature")
    key = hmac.new(secret.encode(), b"SEO-MASTER-RECEIPT-KEY-V2", hashlib.sha256).digest()
    unsigned = {name: item for name, item in value.items() if name != "signature"}
    expected = hmac.new(key, ("SEO-MASTER-RECEIPT-V2\n" + canonical(unsigned)).encode(), hashlib.sha256).hexdigest()
    if not isinstance(signature, str) or not hmac.compare_digest(signature, expected):
        raise ExecutionError("cms_signature_invalid")


class WordPressV2:
    def __init__(self, project_id: str, binding: WordPressBinding, secret: str, receipt_secret: str, *, http: CMSHttpClient | None = None):
        if (any(re.fullmatch(r"[0-9a-f]{64,128}", value) is None or len(set(value)) < 8 for value in (secret, receipt_secret))
                or hmac.compare_digest(secret, receipt_secret)):
            raise ExecutionError("cms_key_unconfigured")
        self.project_id, self.binding, self.secret = project_id, binding, secret
        self.receipt_secret = receipt_secret
        self.http = http or CMSHttpClient()
        parsed = parse_scope(binding.base_url)
        if parsed.origin.split(":", 1)[0] != "https" and not (http and http.policy.allow_private_hosts):
            raise ExecutionError("cms_https_required")

    async def call(self, route: str, payload: dict[str, Any] | None, *, operation_id: str = "-") -> dict[str, Any]:
        method, body = ("GET", b"") if payload is None else ("POST", canonical(payload).encode())
        stamp, nonce = str(int(time.time())), secrets.token_urlsafe(32)
        message = "\n".join(["SEO-MASTER-HMAC-V2", method, route, hashlib.sha256(body).hexdigest(), stamp,
                             nonce, self.binding.key_id, self.project_id, self.binding.site_id, operation_id])
        headers = {"Content-Type": "application/json", "X-SEO-Project-ID": self.project_id,
                   "X-SEO-Site-ID": self.binding.site_id, "X-SEO-Key-ID": self.binding.key_id,
                   "X-SEO-Timestamp": stamp, "X-SEO-Nonce": nonce, "X-SEO-Operation-ID": operation_id,
                   "X-SEO-Signature": hmac.new(self.secret.encode(), message.encode(), hashlib.sha256).hexdigest()}
        status, response = await self.http.request(method, self.binding.base_url.rstrip("/") + "/wp-json" + route, body, headers)
        if status == 404 and payload is None:
            raise ExecutionError("cms_operation_not_found")
        if status != 200:
            code = decode(response).get("code", "unknown")
            safe_code = code if isinstance(code, str) and re.fullmatch(r"[a-z_]{1,64}", code) else "unknown"
            raise ExecutionError(f"cms_request_rejected:{status}:{safe_code}")
        return decode(response)

    async def preflight(self, version: dict[str, Any]) -> dict[str, Any]:
        target = version["target"]
        entity = target.get("entity")
        if (not isinstance(entity, dict) or entity.get("kind") not in {"post", "page"}
                or not isinstance(entity.get("id"), str) or not entity["id"].isdigit()
                or not 1 <= int(entity["id"]) <= 2147483647
                or parse_scope(self.binding.base_url).origin != target["origin"]):
            raise ExecutionError("managed_cms_target_required")
        challenge = secrets.token_hex(32)
        answer = await self.call("/seo-master/v2/challenge", {"project_id": self.project_id, "site_id": self.binding.site_id, "challenge": challenge})
        verify_signed(answer, self.receipt_secret)
        if (answer.get("protocol") != "seo-master/v2" or answer.get("kind") != "challenge"
                or answer.get("challenge") != challenge or answer.get("site_id") != self.binding.site_id
                or answer.get("project_id") != self.project_id or answer.get("key_id") != self.binding.key_id
                or datetime.fromisoformat(answer["expires_at"].replace("Z", "+00:00")) <= datetime.now(timezone.utc)):
            raise ExecutionError("cms_pairing_invalid")
        # Public REST link binds the reviewed URL to the exact published entity.
        kind = "posts" if entity["kind"] == "post" else "pages"
        status, raw = await self.http.request("GET", self.binding.base_url.rstrip("/") + f"/wp-json/wp/v2/{kind}/{entity['id']}?_fields=id,link,status,type", b"", {})
        public = decode(raw)
        if (status != 200 or not isinstance(public.get("id"), int) or isinstance(public.get("id"), bool) or public["id"] != int(entity["id"])
                or public.get("link") != target["url"] or public.get("status") != "publish" or public.get("type") != entity["kind"]):
            raise ExecutionError("cms_target_url_mismatch")
        result = await self.call("/seo-master/v2/snapshot", {"project_id": self.project_id,
                                 "site_id": self.binding.site_id, "entity_id": int(entity["id"])})
        before = snapshot(result.get("snapshot"))
        if result.get("digest") != digest(before):
            raise ExecutionError("cms_snapshot_digest_invalid")
        return before

    async def dry_run(self, record: dict[str, Any]) -> None:
        result = await self.call("/seo-master/v2/target-bound/dry-run", record["command"], operation_id=record["operation_id"])
        if (result.get("outcome") != "ready" or result.get("before") != record["before"] or result.get("after") != record["after"]
                or result.get("before_digest") != digest(record["before"]) or result.get("after_digest") != digest(record["after"])
                or result.get("proposal_hash") != record["command"]["proposal_hash"]):
            raise ExecutionError("cms_dry_run_conflict")

    async def apply(self, record: dict[str, Any]) -> dict[str, Any]:
        receipt = await self.call("/seo-master/v2/target-bound/apply", record["command"], operation_id=record["operation_id"])
        return validate_receipt(receipt, record["command"], record["before"], record["after"], secret=self.receipt_secret, key_id=self.binding.key_id)

    async def status(self, record: dict[str, Any]) -> dict[str, Any]:
        receipt = await self.call("/seo-master/v2/operations/" + record["operation_id"], None, operation_id=record["operation_id"])
        return validate_receipt(receipt, record["command"], record["before"], record["after"], secret=self.receipt_secret, key_id=self.binding.key_id)
