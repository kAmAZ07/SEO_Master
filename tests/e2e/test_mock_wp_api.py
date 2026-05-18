import hashlib
import hmac
import importlib.util
import json
import os
import time
from pathlib import Path

from fastapi.testclient import TestClient


os.environ.setdefault("E2E_PROJECT_ID", "e2e-project")
os.environ.setdefault("E2E_WORDPRESS_HMAC_SECRET", "e2e-wordpress-secret")

MODULE_PATH = Path(__file__).resolve().parent / "mocks" / "mock_wp_api.py"
SPEC = importlib.util.spec_from_file_location("mock_wp_api_under_test", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)

client = TestClient(MODULE.app)


def _signed_headers(path: str, body: bytes, signature_secret: str = "e2e-wordpress-secret") -> dict[str, str]:
    timestamp = str(int(time.time()))
    body_hash = hashlib.sha256(body).hexdigest()
    message = f"{timestamp}PATCH{path}{body_hash}".encode("utf-8")
    signature = hmac.new(signature_secret.encode("utf-8"), message, hashlib.sha256).hexdigest()
    return {
        "X-Project-ID": "e2e-project",
        "X-Timestamp": timestamp,
        "X-Signature": signature,
        "Content-Type": "application/json",
    }


def test_mock_wordpress_health_and_post_listing():
    health = client.get("/wp-json/seo-master/v1/health")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"

    posts = client.get("/wp-json/wp/v2/posts?slug=e2e-seo-post")
    assert posts.status_code == 200
    assert posts.json()[0]["slug"] == "e2e-seo-post"


def test_mock_wordpress_applies_signed_meta_patch():
    payload = {
        "project_id": "e2e-project",
        "entity_id": "1",
        "entity_type": "wordpress_post",
        "changes": [
            {"op": "replace", "path": "/meta_description", "value": "Mock WP signed meta"},
            {"op": "replace", "path": "/h1", "value": "Mock WP signed H1"},
        ],
        "metadata": {"source": "unit-check"},
    }
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

    response = client.patch(
        "/wp-json/seo-master/v1/meta",
        content=body,
        headers=_signed_headers("/wp-json/seo-master/v1/meta", body),
    )

    assert response.status_code == 200
    assert response.json()["status"] == "applied"

    html = client.get("/?p=1")
    assert html.status_code == 200
    assert "Mock WP signed meta" in html.text
    assert "Mock WP signed H1" in html.text


def test_mock_wordpress_rejects_invalid_signature():
    payload = {
        "project_id": "e2e-project",
        "entity_id": "1",
        "entity_type": "wordpress_post",
        "changes": [{"op": "replace", "path": "/meta_description", "value": "Should not apply"}],
    }
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

    response = client.patch(
        "/wp-json/seo-master/v1/meta",
        content=body,
        headers=_signed_headers("/wp-json/seo-master/v1/meta", body, signature_secret="wrong-secret"),
    )

    assert response.status_code == 401
