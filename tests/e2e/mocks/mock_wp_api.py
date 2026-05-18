import hashlib
import hmac
import json
import os
import time
from typing import Any, Dict, List

from fastapi import FastAPI, Header, HTTPException, Request, status
from fastapi.responses import HTMLResponse


PROJECT_ID = os.getenv("E2E_PROJECT_ID", "e2e-project")
HMAC_SECRET = os.getenv("E2E_WORDPRESS_HMAC_SECRET", "e2e-wordpress-secret")
MAX_TIMESTAMP_DRIFT_SECONDS = int(os.getenv("E2E_WORDPRESS_MAX_DRIFT_SECONDS", "300"))

app = FastAPI(title="SEO Master Mock WordPress", version="1.0.0")

_POSTS: Dict[str, Dict[str, Any]] = {
    "1": {
        "id": 1,
        "slug": "e2e-seo-post",
        "title": "E2E SEO Post",
        "meta_description": "Initial E2E meta description",
        "h1": "Initial E2E H1",
        "content": "Mock WordPress content for extended e2e checks.",
        "schema": None,
        "internal_links": [],
    }
}


def _find_post(entity_id: str) -> Dict[str, Any]:
    post = _POSTS.get(str(entity_id))
    if not post:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Post not found")
    return post


def _body_hash(body: bytes) -> str:
    return hashlib.sha256(body or b"").hexdigest()


def _expected_signature(timestamp: str, method: str, path: str, body: bytes) -> str:
    message = f"{timestamp}{method.upper()}{path}{_body_hash(body)}".encode("utf-8")
    return hmac.new(HMAC_SECRET.encode("utf-8"), message, hashlib.sha256).hexdigest()


def _verify_signature(
    *,
    request: Request,
    body: bytes,
    x_project_id: str,
    x_timestamp: str,
    x_signature: str,
) -> None:
    if x_project_id != PROJECT_ID:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid project")

    try:
        ts = int(x_timestamp)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid timestamp") from exc

    if abs(int(time.time()) - ts) > MAX_TIMESTAMP_DRIFT_SECONDS:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Timestamp drift too large")

    path = request.url.path
    if request.url.query:
        path = f"{path}?{request.url.query}"

    normalized_signature = x_signature.strip()
    if normalized_signature.lower().startswith("sha256="):
        normalized_signature = normalized_signature.split("=", 1)[1]

    expected = _expected_signature(x_timestamp, request.method, path, body)
    if not hmac.compare_digest(expected, normalized_signature):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid signature")


def _set_patch_value(post: Dict[str, Any], path: str, value: Any) -> None:
    normalized_path = path.strip().lower()
    if normalized_path in {"/meta_description", "/description", "/meta/description"}:
        post["meta_description"] = "" if value is None else str(value)
    elif normalized_path in {"/h1", "/heading", "/headline"}:
        post["h1"] = "" if value is None else str(value)
    elif normalized_path in {"/title", "/meta_title", "/meta/title"}:
        post["title"] = "" if value is None else str(value)
    elif normalized_path in {"/schema", "/schema_org", "/jsonld"}:
        post["schema"] = value
    elif normalized_path in {"/internal_links", "/interlinks", "/links"}:
        post["internal_links"] = value if isinstance(value, list) else [value]


def _apply_patch(post: Dict[str, Any], changes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    applied: List[Dict[str, Any]] = []
    for change in changes:
        op = str(change.get("op") or "").lower()
        path = str(change.get("path") or "")
        if op in {"add", "replace", "copy", "test"}:
            _set_patch_value(post, path, change.get("value"))
            applied.append({"op": op, "path": path})
        elif op == "remove":
            _set_patch_value(post, path, "")
            applied.append({"op": op, "path": path})
    return applied


def _post_link(post: Dict[str, Any]) -> str:
    return f"http://localhost:8086/?p={post['id']}"


@app.get("/wp-json/seo-master/v1/health")
async def health() -> Dict[str, Any]:
    return {
        "status": "ok",
        "plugin": "seo-master-connector",
        "mode": "e2e-mock",
        "project_id": PROJECT_ID,
    }


@app.get("/wp-json/wp/v2/posts")
async def list_posts(slug: str | None = None) -> List[Dict[str, Any]]:
    posts = list(_POSTS.values())
    if slug:
        posts = [post for post in posts if post["slug"] == slug]

    return [
        {
            "id": post["id"],
            "slug": post["slug"],
            "link": _post_link(post),
            "title": {"rendered": post["title"]},
            "excerpt": {"rendered": post["meta_description"]},
        }
        for post in posts
    ]


@app.get("/", response_class=HTMLResponse)
async def render_post(p: str | None = None) -> str:
    post_id = str(p or "1")
    post = _find_post(post_id)
    schema = ""
    if post.get("schema"):
        schema = f'<script type="application/ld+json">{json.dumps(post["schema"], ensure_ascii=False)}</script>'

    links = ""
    for item in post.get("internal_links") or []:
        if isinstance(item, dict):
            url = str(item.get("url") or "#")
            anchor = str(item.get("anchor") or item.get("text") or url)
            links += f'<a href="{url}">{anchor}</a>'

    return f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <title>{post["title"]}</title>
  <meta name="description" content="{post["meta_description"]}">
  {schema}
</head>
<body>
  <main>
    <h1>{post["h1"]}</h1>
    <article>{post["content"]}</article>
    <nav>{links}</nav>
  </main>
</body>
</html>"""


@app.patch("/wp-json/seo-master/v1/{change_type}")
async def apply_patch(
    change_type: str,
    request: Request,
    x_project_id: str = Header(..., alias="X-Project-ID"),
    x_timestamp: str = Header(..., alias="X-Timestamp"),
    x_signature: str = Header(..., alias="X-Signature"),
    x_correlation_id: str | None = Header(None, alias="X-Correlation-ID"),
) -> Dict[str, Any]:
    if change_type not in {"meta", "schema", "interlinks"}:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unsupported change type")

    body = await request.body()
    _verify_signature(
        request=request,
        body=body,
        x_project_id=x_project_id,
        x_timestamp=x_timestamp,
        x_signature=x_signature,
    )

    payload = json.loads(body.decode("utf-8") or "{}")
    post = _find_post(str(payload.get("entity_id")))
    changes = payload.get("changes") or []
    applied = _apply_patch(post, changes if isinstance(changes, list) else [])

    return {
        "status": "applied",
        "platform": "wordpress",
        "change_type": change_type,
        "entity_id": str(post["id"]),
        "applied": applied,
        "correlation_id": x_correlation_id,
        "warnings": [],
    }
