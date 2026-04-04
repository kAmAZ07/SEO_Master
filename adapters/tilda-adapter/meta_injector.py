import html
import json
from typing import Any, Dict, List, Tuple

from config import settings
from tilda_api_client import TildaAPIClient


class TildaMetaInjector:
    MANAGED_START = '<!-- seo-master:managed:start -->'
    MANAGED_END = '<!-- seo-master:managed:end -->'
    SCHEMA_START = '<!-- seo-master:schema:start -->'
    SCHEMA_END = '<!-- seo-master:schema:end -->'
    INTERLINKS_START = '<!-- seo-master:internal-links:start -->'
    INTERLINKS_END = '<!-- seo-master:internal-links:end -->'

    def __init__(self, client: TildaAPIClient | None = None) -> None:
        self.client = client or TildaAPIClient()

    async def apply_fields(self, page_id: str, fields: Dict[str, Any]) -> Dict[str, Any]:
        if not fields:
            return {'status': 'skipped', 'reason': 'no_fields'}

        return await self.client.update_page(page_id, fields)

    async def _get_current_customhtml(self, page_id: str) -> str:
        page = await self.client.get_page(page_id)
        result = page.get('result')
        if not isinstance(result, dict):
            return ''

        custom_html = result.get('customhtml')
        if isinstance(custom_html, str):
            return custom_html
        return ''

    def _replace_or_append_block(
        self,
        html_text: str,
        start_marker: str,
        end_marker: str,
        new_block: str,
    ) -> str:
        start_index = html_text.find(start_marker)
        end_index = html_text.find(end_marker)

        if start_index != -1 and end_index != -1 and end_index >= start_index:
            end_index += len(end_marker)
            return f'{html_text[:start_index]}{new_block}{html_text[end_index:]}'

        if not html_text.strip():
            return new_block

        return f'{html_text.rstrip()}\n{new_block}'

    def _extract_managed_content(self, custom_html: str) -> Tuple[str, str]:
        start_index = custom_html.find(self.MANAGED_START)
        end_index = custom_html.find(self.MANAGED_END)

        if start_index == -1 or end_index == -1 or end_index < start_index:
            return custom_html.strip(), ''

        managed_end = end_index + len(self.MANAGED_END)
        unmanaged = f'{custom_html[:start_index]}{custom_html[managed_end:]}'
        managed = custom_html[start_index:managed_end]
        return unmanaged.strip(), managed

    def _wrap_managed_block(self, managed_content: str) -> str:
        if not managed_content.strip():
            return ''
        return f'{self.MANAGED_START}\n{managed_content.strip()}\n{self.MANAGED_END}'

    def _merge_managed_block(
        self,
        existing_custom_html: str,
        start_marker: str,
        end_marker: str,
        new_block: str,
    ) -> str:
        unmanaged_html, managed_html = self._extract_managed_content(existing_custom_html)
        managed_inner = managed_html
        if managed_inner:
            managed_inner = managed_inner.replace(self.MANAGED_START, '', 1)
            managed_inner = managed_inner.rsplit(self.MANAGED_END, 1)[0].strip()

        updated_managed = self._replace_or_append_block(
            managed_inner,
            start_marker,
            end_marker,
            new_block,
        )
        wrapped_managed = self._wrap_managed_block(updated_managed)

        if unmanaged_html and wrapped_managed:
            return f'{unmanaged_html}\n{wrapped_managed}'
        if unmanaged_html:
            return unmanaged_html
        return wrapped_managed

    def _render_schema_block(self, schema_value: str) -> str:
        return (
            f'{self.SCHEMA_START}\n'
            f'<script type="application/ld+json">{schema_value}</script>\n'
            f'{self.SCHEMA_END}'
        )

    def _render_interlinks_block(self, links: List[Dict[str, Any]]) -> str:
        rendered = []
        for link in links:
            href = link.get('target_url') or link.get('url')
            anchor = link.get('anchor_text') or link.get('anchor') or href
            if not href:
                continue
            rendered.append(f'<a href="{html.escape(str(href), quote=True)}">{html.escape(str(anchor))}</a>')

        if not rendered:
            return ''

        return (
            f'{self.INTERLINKS_START}\n'
            f'{"".join(rendered)}\n'
            f'{self.INTERLINKS_END}'
        )

    def _build_customhtml_warnings(self, existing_custom_html: str) -> List[Dict[str, str]]:
        warnings: List[Dict[str, str]] = []
        unmanaged_html, _managed_html = self._extract_managed_content(existing_custom_html)
        lowered_unmanaged = unmanaged_html.lower()

        if unmanaged_html.strip():
            warnings.append(
                {
                    'code': 'unmanaged_customhtml_preserved',
                    'message': 'Existing custom HTML was preserved outside the SEO Master managed block.',
                }
            )

        if '<script' in lowered_unmanaged and 'application/ld+json' in lowered_unmanaged:
            warnings.append(
                {
                    'code': 'external_jsonld_present',
                    'message': 'Detected JSON-LD outside the SEO Master managed block; review for duplicate schema.',
                }
            )

        return warnings

    def _schema_policy(self) -> str:
        return str(getattr(settings, 'schema_policy', 'warn') or 'warn').lower()

    def _schema_policy_result(self, warnings: List[Dict[str, str]]) -> Dict[str, Any] | None:
        if not warnings:
            return None

        warning_codes = {warning.get('code') for warning in warnings}
        policy = self._schema_policy()

        if policy == 'strict':
            return {
                'status': 'blocked',
                'reason': 'schema_policy_strict',
                'warnings': warnings,
                'requires_hitl': True,
            }

        if policy == 'require_hitl' and 'external_jsonld_present' in warning_codes:
            return {
                'status': 'requires_hitl',
                'reason': 'external_jsonld_present',
                'warnings': warnings,
                'requires_hitl': True,
            }

        return None

    async def _merge_custom_html_block(
        self,
        page_id: str,
        start_marker: str,
        end_marker: str,
        new_block: str,
    ) -> Dict[str, Any]:
        existing_custom_html = await self._get_current_customhtml(page_id)
        merged_custom_html = self._merge_managed_block(
            existing_custom_html,
            start_marker,
            end_marker,
            new_block,
        )
        update_result = await self.apply_fields(page_id, {'customhtml': merged_custom_html})
        warnings = self._build_customhtml_warnings(existing_custom_html)
        if warnings:
            update_result = {
                **update_result,
                'warnings': warnings,
            }
        return update_result

    async def apply_meta(self, page_id: str, meta: Dict[str, Any]) -> Dict[str, Any]:
        fields: Dict[str, Any] = {}

        if 'title' in meta:
            fields['title'] = meta['title']
        if 'description' in meta:
            fields['descr'] = meta['description']
        if 'h1' in meta:
            fields['h1'] = meta['h1']

        meta_result = await self.apply_fields(page_id, fields)
        if 'schema' not in meta:
            return meta_result

        schema_result = await self.apply_schema(page_id, meta['schema'])
        return {
            'status': 'ok',
            'meta_result': meta_result,
            'schema_result': schema_result,
        }

    async def apply_schema(self, page_id: str, schema_value: str) -> Dict[str, Any]:
        if not schema_value:
            return {'status': 'skipped', 'reason': 'no_schema'}

        if isinstance(schema_value, (dict, list)):
            schema_payload = json.dumps(schema_value, ensure_ascii=False)
        else:
            schema_payload = str(schema_value)

        existing_custom_html = await self._get_current_customhtml(page_id)
        warnings = self._build_customhtml_warnings(existing_custom_html)
        policy_result = self._schema_policy_result(warnings)
        if policy_result is not None:
            return policy_result

        block = self._render_schema_block(schema_payload)
        merged_custom_html = self._merge_managed_block(
            existing_custom_html,
            self.SCHEMA_START,
            self.SCHEMA_END,
            block,
        )
        update_result = await self.apply_fields(page_id, {'customhtml': merged_custom_html})
        if warnings:
            update_result = {
                **update_result,
                'warnings': warnings,
            }
        return update_result

    async def apply_interlinks(self, page_id: str, links: List[Dict[str, Any]]) -> Dict[str, Any]:
        if not links:
            return {'status': 'skipped', 'reason': 'no_links'}

        html_block = self._render_interlinks_block(links)
        if not html_block:
            return {'status': 'skipped', 'reason': 'no_valid_links'}

        return await self._merge_custom_html_block(
            page_id,
            self.INTERLINKS_START,
            self.INTERLINKS_END,
            html_block,
        )
