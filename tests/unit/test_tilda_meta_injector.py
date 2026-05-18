import importlib.util
import sys
import types
from pathlib import Path

import pytest


ADAPTER_DIR = Path(__file__).resolve().parents[2] / 'adapters' / 'tilda-adapter'
ADAPTER_PATH = str(ADAPTER_DIR)
sys.path.insert(0, ADAPTER_PATH)

fake_config = types.ModuleType('config')
fake_config.settings = types.SimpleNamespace(
    base_url='https://api.tildacdn.info/v1',
    request_timeout_seconds=20.0,
    public_key=None,
    secret_key=None,
    mock_mode=True,
    schema_policy='warn',
)
previous_config = sys.modules.get('config')
sys.modules['config'] = fake_config

MODULE_PATH = ADAPTER_DIR / 'meta_injector.py'
SPEC = importlib.util.spec_from_file_location('tilda_meta_injector', MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
try:
    SPEC.loader.exec_module(MODULE)
finally:
    if previous_config is None:
        sys.modules.pop('config', None)
    else:
        sys.modules['config'] = previous_config
    try:
        sys.path.remove(ADAPTER_PATH)
    except ValueError:
        pass
TildaMetaInjector = MODULE.TildaMetaInjector


class ClientStub:
    def __init__(self, initial_customhtml=''):
        self.page = {'customhtml': initial_customhtml}
        self.updated_fields = None

    async def get_page(self, page_id):
        return {'result': {'pageid': page_id, **self.page}}

    async def update_page(self, page_id, fields):
        self.updated_fields = fields
        self.page.update(fields)
        return {'status': 'mock_applied', 'pageid': page_id, 'updated_fields': fields}


def make_injector(initial_customhtml='', schema_policy='warn'):
    fake_config.settings.schema_policy = schema_policy
    client = ClientStub(initial_customhtml)
    return TildaMetaInjector(client=client), client


@pytest.mark.asyncio
async def test_apply_schema_preserves_existing_custom_html():
    injector, client = make_injector('<div>User HTML</div>')

    result = await injector.apply_schema('page-1', '{"@context":"https://schema.org"}')

    assert result['status'] == 'mock_applied'
    assert result['warnings'][0]['code'] == 'unmanaged_customhtml_preserved'
    custom_html = client.updated_fields['customhtml']
    assert '<div>User HTML</div>' in custom_html
    assert '<!-- seo-master:schema:start -->' in custom_html
    assert '<script type="application/ld+json">' in custom_html
    assert '{"@context":"https://schema.org"}' in custom_html


@pytest.mark.asyncio
async def test_apply_interlinks_preserves_existing_schema_block():
    existing = (
        '<div>User HTML</div>\n'
        '<!-- seo-master:managed:start -->\n'
        '<!-- seo-master:schema:start -->\n'
        '<script type="application/ld+json">{"name":"Example"}</script>\n'
        '<!-- seo-master:schema:end -->\n'
        '<!-- seo-master:managed:end -->'
    )
    injector, client = make_injector(existing)

    result = await injector.apply_interlinks(
        'page-1',
        [{'target_url': 'https://example.com/a', 'anchor_text': 'Example A'}],
    )

    assert result['status'] == 'mock_applied'
    custom_html = client.updated_fields['customhtml']
    assert '{"name":"Example"}' in custom_html
    assert '<!-- seo-master:internal-links:start -->' in custom_html
    assert 'https://example.com/a' in custom_html
    assert 'Example A' in custom_html


@pytest.mark.asyncio
async def test_apply_schema_replaces_only_managed_schema_block():
    existing = (
        '<div>User HTML</div>\n'
        '<!-- seo-master:managed:start -->\n'
        '<!-- seo-master:schema:start -->\n'
        '<script type="application/ld+json">{"name":"Old"}</script>\n'
        '<!-- seo-master:schema:end -->\n'
        '<!-- seo-master:internal-links:start -->\n'
        '<a href="https://example.com/old">Old link</a>\n'
        '<!-- seo-master:internal-links:end -->\n'
        '<!-- seo-master:managed:end -->'
    )
    injector, client = make_injector(existing)

    await injector.apply_schema('page-1', '{"name":"New"}')

    custom_html = client.updated_fields['customhtml']
    assert '{"name":"New"}' in custom_html
    assert '{"name":"Old"}' not in custom_html
    assert 'https://example.com/old' in custom_html


@pytest.mark.asyncio
async def test_apply_schema_warns_about_external_jsonld():
    existing = (
        '<script type="application/ld+json">{"name":"External"}</script>\n'
        '<div>User HTML</div>'
    )
    injector, _client = make_injector(existing, schema_policy='warn')

    result = await injector.apply_schema('page-1', '{"name":"Managed"}')

    warning_codes = {warning['code'] for warning in result['warnings']}
    assert result['status'] == 'mock_applied'
    assert 'unmanaged_customhtml_preserved' in warning_codes
    assert 'external_jsonld_present' in warning_codes


@pytest.mark.asyncio
async def test_apply_schema_requires_hitl_when_policy_demands_it():
    existing = (
        '<script type="application/ld+json">{"name":"External"}</script>\n'
        '<div>User HTML</div>'
    )
    injector, client = make_injector(existing, schema_policy='require_hitl')

    result = await injector.apply_schema('page-1', '{"name":"Managed"}')

    assert result['status'] == 'requires_hitl'
    assert result['requires_hitl'] is True
    assert result['reason'] == 'external_jsonld_present'
    assert client.updated_fields is None


@pytest.mark.asyncio
async def test_apply_schema_blocks_in_strict_mode_on_any_warning():
    injector, client = make_injector('<div>User HTML</div>', schema_policy='strict')

    result = await injector.apply_schema('page-1', '{"name":"Managed"}')

    assert result['status'] == 'blocked'
    assert result['requires_hitl'] is True
    assert result['reason'] == 'schema_policy_strict'
    assert client.updated_fields is None
