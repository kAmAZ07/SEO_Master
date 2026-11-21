"""Only documented GET methods; errors never contain credential-bearing URLs."""
import json
from typing import Any, Protocol
from urllib.parse import urlencode

from config import settings
from shared.netpolicy import FetchResult, NetPolicy, NetPolicyError, SafeHttpClient
from shared.tilda_contract import ReadMethod, install_transport_redaction, unsupported

install_transport_redaction()

BASE_URL = 'https://api.tildacdn.info/v1'


class TildaAPIError(ValueError):
    def __init__(self, code: str, retry_after: int = 0) -> None:
        self.code, self.retry_after = code, retry_after
        super().__init__(code)


class Reader(Protocol):
    async def fetch(self, url: str) -> FetchResult: ...


class TildaAPIClient:
    def __init__(self, *, public_key: str | None = None, secret_key: str | None = None,
                 base_url: str | None = None, timeout: float | None = None, mock_mode: bool | None = None,
                 reader: Reader | None = None) -> None:
        if (base_url or settings.base_url).rstrip('/') != BASE_URL:
            raise TildaAPIError('tilda_base_url_unsupported')
        self.public_key, self.secret_key = public_key, secret_key
        self.mock_mode = settings.mock_mode if mock_mode is None else mock_mode
        budget = min(timeout or settings.request_timeout_seconds, 20.)
        self.reader = reader or SafeHttpClient(policy=NetPolicy(allowed_schemes=frozenset({'https'}),
            allowed_ports=frozenset({443}), max_redirects=0, total_timeout=budget, max_body_bytes=2 * 1024 * 1024))

    async def read(self, method: ReadMethod, *, project_id: str | None = None, page_id: str | None = None) -> dict[str, Any] | list[Any]:
        if self.mock_mode:
            raise TildaAPIError('tilda_read_disabled')
        if not self.public_key or not self.secret_key or max(len(self.public_key), len(self.secret_key)) > 256:
            raise TildaAPIError('tilda_credentials_missing')
        if method not in {'getprojectslist', 'getprojectinfo', 'getpageslist', 'getpage'}:
            raise TildaAPIError('tilda_read_method_unsupported')
        params = {'publickey': self.public_key, 'secretkey': self.secret_key}
        for name, identity_value in (('projectid', project_id), ('pageid', page_id)):
            if identity_value is not None:
                if not identity_value.isascii() or not identity_value.isdigit() or len(identity_value) > 20:
                    raise TildaAPIError('tilda_identity_invalid')
                params[name] = identity_value
        if method in {'getprojectinfo', 'getpageslist'} and project_id is None or method == 'getpage' and page_id is None:
            raise TildaAPIError('tilda_identity_required')
        try:
            result = await self.reader.fetch(BASE_URL + '/' + method + '/?' + urlencode(params))
        except NetPolicyError:
            raise TildaAPIError('tilda_upstream_unavailable') from None
        if result.status == 429:
            raise TildaAPIError('tilda_quota_exceeded', 3600)
        if result.status in {401, 403}:
            raise TildaAPIError('tilda_credentials_rejected')
        if result.status != 200:
            raise TildaAPIError('tilda_upstream_unavailable')
        try:
            data = json.loads(result.body)
        except (ValueError, UnicodeError):
            raise TildaAPIError('tilda_upstream_invalid_response') from None
        if not isinstance(data, dict):
            raise TildaAPIError('tilda_upstream_invalid_response')
        if data.get('status') == 'ERROR':
            message = str(data.get('message', '')).lower()[:512]
            if any(word in message for word in ('limit', 'quota', '150')):
                raise TildaAPIError('tilda_quota_exceeded', 3600)
            raise TildaAPIError('tilda_upstream_error')
        value = data.get('result')
        if data.get('status') != 'FOUND':
            raise TildaAPIError('tilda_upstream_invalid_response')
        if method in {'getprojectslist', 'getpageslist'}:
            if not isinstance(value, list):
                raise TildaAPIError('tilda_upstream_invalid_response')
            return value
        if not isinstance(value, dict):
            raise TildaAPIError('tilda_upstream_invalid_response')
        if method == 'getprojectinfo' and str(value.get('id')) != project_id:
            raise TildaAPIError('tilda_scope_mismatch')
        if method == 'getpage' and (str(value.get('id')) != page_id or project_id is None or str(value.get('projectid')) != project_id):
            raise TildaAPIError('tilda_scope_mismatch')
        return value

    async def validate_credentials(self) -> dict[str, object]:
        result = await self.read('getprojectslist')
        return {'status': 'FOUND', 'ready': True, 'result': result}

    async def get_page(self, page_id: str, project_id: str) -> dict[str, Any] | list[Any]:
        return await self.read('getpage', project_id=project_id, page_id=page_id)

    async def update_page(self, page_id: str, fields: object) -> dict[str, object]:
        return unsupported(page_id)
