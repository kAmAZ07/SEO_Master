import hashlib
import hmac
import json
import os
import time
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlencode, urlparse

import httpx
import pytest

pytestmark = [pytest.mark.e2e]

if os.getenv('RUN_E2E') != '1':
    pytest.skip('Set RUN_E2E=1 to execute extended e2e tests', allow_module_level=True)


def _http_request(
    method: str,
    url: str,
    *,
    json_payload: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, str]] = None,
    timeout: int = 30,
) -> Tuple[int, Any, bytes]:
    payload_bytes: Optional[bytes] = None
    request_headers: Dict[str, str] = {'Accept': 'application/json'}
    if headers:
        request_headers.update(headers)

    if json_payload is not None:
        payload_bytes = json.dumps(json_payload, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
        request_headers['Content-Type'] = 'application/json'

    try:
        request_kwargs: Dict[str, Any] = {
            "headers": request_headers,
            "timeout": timeout,
            "trust_env": False,
        }
        if payload_bytes is not None:
            request_kwargs["content"] = payload_bytes
        response = httpx.request(method.upper(), url, **request_kwargs)
    except httpx.HTTPError as exc:
        raise AssertionError(f'Network error for {method} {url}: {exc}') from exc

    raw = response.content
    if response.status_code >= 400:
        body_text = raw.decode('utf-8', errors='replace') if raw else ''
        raise AssertionError(f'HTTP {response.status_code} for {method} {url}: {body_text[:500]}')

    content_type = response.headers.get('Content-Type', '')
    if 'application/json' in content_type and raw:
        return response.status_code, response.json(), raw
    return response.status_code, raw.decode('utf-8', errors='replace'), raw


def _wait_for_json(url: str, expected_key: str, expected_value: Any, timeout_seconds: int = 180) -> Dict[str, Any]:
    deadline = time.time() + timeout_seconds
    last_error = ''
    while time.time() < deadline:
        try:
            status, payload, _ = _http_request('GET', url, timeout=10)
            if status == 200 and isinstance(payload, dict) and payload.get(expected_key) == expected_value:
                return payload
            last_error = f'Unexpected payload: {payload}'
        except AssertionError as exc:
            last_error = str(exc)
        time.sleep(2)

    raise AssertionError(f'Timeout waiting for {url}. Last error: {last_error}')


def _wait_for_condition(check_fn, timeout_seconds: int = 180, sleep_seconds: int = 2) -> Any:
    deadline = time.time() + timeout_seconds
    last_error = ''
    while time.time() < deadline:
        try:
            value = check_fn()
            if value:
                return value
            last_error = 'condition returned false'
        except AssertionError as exc:
            last_error = str(exc)
        time.sleep(sleep_seconds)

    raise AssertionError(f'Timeout waiting for condition. Last error: {last_error}')


def _build_hmac_headers(method: str, url: str, body: bytes, *, project_id: str, secret: str) -> Dict[str, str]:
    timestamp = str(int(time.time()))
    parsed = urlparse(url)
    path_with_query = parsed.path or '/'
    if parsed.query:
        path_with_query = f'{path_with_query}?{parsed.query}'

    body_hash = hashlib.sha256(body).hexdigest()
    message = f'{timestamp}{method.upper()}{path_with_query}{body_hash}'.encode('utf-8')
    signature = hmac.new(secret.encode('utf-8'), message, hashlib.sha256).hexdigest()

    return {
        'X-Project-ID': project_id,
        'X-Timestamp': timestamp,
        'X-Signature': signature,
        'Content-Type': 'application/json',
    }


def _signed_patch(
    url: str,
    payload: Dict[str, Any],
    *,
    project_id: str,
    secret: str,
    timeout: int = 30,
) -> Dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    headers = _build_hmac_headers('PATCH', url, body, project_id=project_id, secret=secret)

    try:
        response = httpx.patch(url, content=body, headers=headers, timeout=timeout, trust_env=False)
    except httpx.HTTPError as exc:
        raise AssertionError(f'HMAC PATCH network error: {exc}') from exc

    if response.status_code >= 400:
        raise AssertionError(f'HMAC PATCH failed {response.status_code}: {response.text[:600]}')

    return response.json() if response.content else {}


@pytest.mark.timeout(900)
def test_extended_e2e_wordpress_tilda_stack():
    api_gateway_url = os.getenv('E2E_API_GATEWAY_URL', 'http://localhost:8000')
    management_url = os.getenv('E2E_MANAGEMENT_URL', 'http://localhost:8004')
    client_gateway_url = os.getenv('E2E_CLIENT_GATEWAY_URL', 'http://localhost:8005')
    tilda_adapter_url = os.getenv('E2E_TILDA_ADAPTER_URL', 'http://localhost:8010')
    wordpress_url = os.getenv('E2E_WORDPRESS_URL', 'http://localhost:8086')

    project_id = os.getenv('E2E_PROJECT_ID', 'e2e-project')
    hmac_secret = os.getenv('E2E_HMAC_SECRET', 'e2e-shared-secret')
    internal_key = os.getenv('E2E_INTERNAL_API_KEY', 'change-me-e2e')

    _wait_for_json(f'{api_gateway_url}/health', 'status', 'healthy', timeout_seconds=180)
    _wait_for_json(f'{management_url}/health', 'status', 'healthy', timeout_seconds=180)
    _wait_for_json(f'{client_gateway_url}/health', 'status', 'healthy', timeout_seconds=180)
    _wait_for_json(f'{tilda_adapter_url}/health/ready', 'status', 'ready', timeout_seconds=180)

    _wait_for_condition(
        lambda: _http_request('GET', f'{wordpress_url}/wp-json/seo-master/v1/health', timeout=10)[1].get('status') == 'ok',
        timeout_seconds=300,
        sleep_seconds=3,
    )

    status_code, posts_payload, _ = _http_request(
        'GET', f'{wordpress_url}/wp-json/wp/v2/posts?{urlencode({"slug": "e2e-seo-post"})}', timeout=20
    )
    assert status_code == 200
    assert isinstance(posts_payload, list) and posts_payload, 'WordPress test post e2e-seo-post is not available'

    post = posts_payload[0]
    post_id = str(post['id'])
    post_link = str(post.get('link') or f'{wordpress_url}/?p={post_id}')

    meta_value = f'E2E meta description {int(time.time())}'
    patch_payload = {
        'project_id': project_id,
        'task_id': 'e2e-meta-patch',
        'entity_id': post_id,
        'entity_type': 'wordpress_post',
        'changes': [
            {'op': 'replace', 'path': '/meta_description', 'value': meta_value},
            {'op': 'replace', 'path': '/h1', 'value': 'SEO E2E H1'},
        ],
        'metadata': {'source': 'e2e'},
    }

    patch_response = _signed_patch(
        f'{client_gateway_url}/api/client/meta',
        patch_payload,
        project_id=project_id,
        secret=hmac_secret,
    )
    assert patch_response.get('status') in {'applied', 'received'}

    def _meta_visible() -> bool:
        _, html_text, _ = _http_request('GET', f'{wordpress_url}/?p={post_id}', timeout=20)
        if not isinstance(html_text, str):
            return False
        return meta_value in html_text

    _wait_for_condition(_meta_visible, timeout_seconds=120, sleep_seconds=3)

    logs_status, logs_payload, _ = _http_request(
        'GET',
        f'{client_gateway_url}/changes/pending/{project_id}?status_filter=applied&limit=100',
        headers={'X-Internal-API-Key': internal_key},
        timeout=20,
    )
    assert logs_status == 200
    assert isinstance(logs_payload, list)
    assert any(item.get('change_type') == 'meta' for item in logs_payload), 'No applied meta deployment log found'

    tilda_payload = {
        'project_id': project_id,
        'task_id': 'e2e-tilda-patch',
        'entity_id': 'tilda-page-1',
        'entity_type': 'tilda_page',
        'changes': [
            {'op': 'replace', 'path': '/meta_description', 'value': 'Tilda E2E description'},
        ],
        'metadata': {'platform': 'tilda', 'source': 'e2e'},
    }
    tilda_response = _signed_patch(
        f'{client_gateway_url}/api/client/meta',
        tilda_payload,
        project_id=project_id,
        secret=hmac_secret,
    )
    assert tilda_response.get('status') in {'applied', 'received'}

    tilda_logs_status, tilda_logs_payload, _ = _http_request(
        'GET',
        f'{client_gateway_url}/changes/pending/{project_id}?status_filter=applied&limit=100',
        headers={'X-Internal-API-Key': internal_key},
        timeout=20,
    )
    assert tilda_logs_status == 200
    assert isinstance(tilda_logs_payload, list)
    assert any(
        item.get('entity_id') == 'tilda-page-1' and item.get('entity_type') == 'tilda_page'
        for item in tilda_logs_payload
    ), 'No applied Tilda deployment log found'

    opt_payload = {
        'project_id': project_id,
        'url': 'http://wordpress/?p=' + post_id,
        'wait': True,
    }
    opt_status, opt_result, _ = _http_request(
        'POST',
        f'{management_url}/internal/optimization/run',
        json_payload=opt_payload,
        timeout=600,
    )
    assert opt_status == 202
    assert isinstance(opt_result, dict)
    assert opt_result.get('status') == 'queued'
    assert opt_result.get('project_id') == project_id
    assert isinstance(opt_result.get('celery_task_id'), str) and opt_result.get('celery_task_id')

    email = f'e2e_{int(time.time())}@example.com'
    reg_status, reg_payload, _ = _http_request(
        'POST',
        f'{api_gateway_url}/api/auth/register',
        json_payload={'email': email, 'password': 'TestPass123!', 'name': 'E2E User'},
        timeout=30,
    )
    assert reg_status == 200
    token = reg_payload.get('token')
    assert isinstance(token, str) and token

    hitl_status, hitl_payload, _ = _http_request(
        'GET',
        f'{api_gateway_url}/api/hitl/tasks?status_filter=pending&limit=10',
        headers={'Authorization': f'Bearer {token}'},
        timeout=30,
    )
    assert hitl_status == 200
    assert isinstance(hitl_payload, list)

    quick_status, quick_payload, _ = _http_request(
        'POST',
        f'{api_gateway_url}/api/public/quick-audit',
        json_payload={'url': 'http://wordpress/?p=' + post_id},
        timeout=30,
    )
    assert quick_status == 200
    audit_uid = quick_payload.get('uid')
    assert isinstance(audit_uid, str) and audit_uid

    def _audit_completed() -> bool:
        _, payload, _ = _http_request('GET', f'{api_gateway_url}/api/public/audit-status/{audit_uid}', timeout=20)
        if not isinstance(payload, dict):
            return False
        status = payload.get('status')
        if status == 'failed':
            raise AssertionError('Public quick audit failed in e2e scenario')
        return status == 'completed'

    _wait_for_condition(_audit_completed, timeout_seconds=420, sleep_seconds=5)
