from typing import Any, Dict

import httpx

from config import settings


_mock_pages: Dict[str, Dict[str, Any]] = {}


class TildaAPIClient:
    def __init__(self) -> None:
        self.base_url = settings.base_url.rstrip('/')
        self.timeout = settings.request_timeout_seconds

    def _auth_params(self) -> Dict[str, str]:
        if not settings.public_key or not settings.secret_key:
            raise ValueError('TILDA_PUBLIC_KEY and TILDA_SECRET_KEY are required')
        return {'publickey': settings.public_key, 'secretkey': settings.secret_key}

    async def validate_credentials(self) -> Dict[str, Any]:
        if settings.mock_mode:
            return {'status': 'mock', 'ready': True}

        params = self._auth_params()
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(f'{self.base_url}/getprojectslist', params=params)
            response.raise_for_status()
            data = response.json()

        return {'status': 'ok', 'ready': True, 'result': data}

    async def get_page(self, page_id: str) -> Dict[str, Any]:
        if settings.mock_mode:
            return {
                'status': 'mock',
                'result': {
                    'pageid': page_id,
                    **_mock_pages.get(page_id, {}),
                },
            }

        params = {**self._auth_params(), 'pageid': page_id}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(f'{self.base_url}/getpage', params=params)
            response.raise_for_status()
            return response.json()

    async def update_page(self, page_id: str, fields: Dict[str, Any]) -> Dict[str, Any]:
        if settings.mock_mode:
            existing = _mock_pages.get(page_id, {})
            existing.update(fields)
            _mock_pages[page_id] = existing
            return {
                'status': 'mock_applied',
                'pageid': page_id,
                'updated_fields': fields,
            }

        payload = {**self._auth_params(), 'pageid': page_id, **fields}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(f'{self.base_url}/updatepage', data=payload)
            response.raise_for_status()
            return response.json()
