from typing import Any, Dict, List

from tilda_api_client import TildaAPIClient


class TildaMetaInjector:
    def __init__(self, client: TildaAPIClient | None = None) -> None:
        self.client = client or TildaAPIClient()

    async def apply_fields(self, page_id: str, fields: Dict[str, Any]) -> Dict[str, Any]:
        if not fields:
            return {'status': 'skipped', 'reason': 'no_fields'}

        return await self.client.update_page(page_id, fields)

    async def apply_meta(self, page_id: str, meta: Dict[str, Any]) -> Dict[str, Any]:
        fields: Dict[str, Any] = {}

        if 'title' in meta:
            fields['title'] = meta['title']
        if 'description' in meta:
            fields['descr'] = meta['description']
        if 'h1' in meta:
            fields['h1'] = meta['h1']
        if 'schema' in meta:
            fields['customhtml'] = meta['schema']

        return await self.apply_fields(page_id, fields)

    async def apply_schema(self, page_id: str, schema_value: str) -> Dict[str, Any]:
        return await self.apply_fields(page_id, {'customhtml': schema_value})

    async def apply_interlinks(self, page_id: str, links: List[Dict[str, Any]]) -> Dict[str, Any]:
        if not links:
            return {'status': 'skipped', 'reason': 'no_links'}

        rendered = []
        for link in links:
            href = link.get('target_url') or link.get('url')
            anchor = link.get('anchor_text') or link.get('anchor') or href
            if not href:
                continue
            rendered.append(f'<a href="{href}">{anchor}</a>')

        if not rendered:
            return {'status': 'skipped', 'reason': 'no_valid_links'}

        html_block = '<!-- seo-master:internal-links -->' + ''.join(rendered)
        return await self.apply_fields(page_id, {'customhtml': html_block})
