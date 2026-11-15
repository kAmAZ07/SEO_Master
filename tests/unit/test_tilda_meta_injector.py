"""Official read/callback protocol fixtures, never evidence of Tilda-native writing."""
import importlib
import logging
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
from urllib.parse import parse_qs, urlsplit

from fastapi import FastAPI
from fastapi.testclient import TestClient
import httpx
import pytest

from shared.netpolicy import FetchResult, NetPolicyError
from shared.tilda_contract import CallbackAccessFilter, TildaTransportFilter

ADAPTER = Path(__file__).resolve().parents[2] / 'adapters/tilda-adapter'
configuration = ModuleType('config')
configuration.settings = SimpleNamespace(base_url='https://api.tildacdn.info/v1', request_timeout_seconds=10.,
    public_key=None, secret_key=None, mock_mode=False, internal_api_key='i' * 32,
    webhook_forward_url='http://client-fixture/internal/tilda/webhook/published', webhook_forward_api_key='f' * 32)
previous = sys.modules.get('config')
sys.modules['config'] = configuration
sys.path.insert(0, str(ADAPTER))
try:
    client_module = importlib.import_module('tilda_api_client')
    injector_module = importlib.import_module('meta_injector')
    routes = importlib.import_module('internal_routes')
    webhooks = importlib.import_module('tilda_webhook_handler')
finally:
    sys.path.remove(str(ADAPTER))
    if previous is None:
        sys.modules.pop('config', None)
    else:
        sys.modules['config'] = previous


class Reader:
    def __init__(self, body=b'{"status":"FOUND","result":[]}', status=200, error=False):
        self.body, self.status, self.error, self.calls = body, status, error, []

    async def fetch(self, url):
        self.calls.append(url)
        if self.error:
            raise NetPolicyError('timeout')
        return FetchResult(url, url, self.status, {}, self.body, [], None, .01)


def make_client(reader=None, **extra):
    return client_module.TildaAPIClient(public_key='fixture-public', secret_key='fixture-secret', reader=reader or Reader(), **extra)


@pytest.mark.asyncio
@pytest.mark.parametrize('method,value', [
    ('apply_meta', {'title': None}), ('apply_meta', {'description': ''}),
    ('apply_schema', {'@context': 'https://schema.org'}), ('apply_schema', '</script><script>alert(1)</script>'),
    ('apply_interlinks', [{'target_url': 'http://127.0.0.1/', 'anchor_text': 'Link'}]),
    ('apply_fields', {'unknown': 'value'}), ('apply_fields', {}),
])
async def test_all_writes_refused_before_any_read_or_mutation(method, value):
    class Forbidden:
        async def get_page(self, *args):
            raise AssertionError('upstream_read_for_write')
        async def update_page(self, *args):
            raise AssertionError('upstream_mutation')
    result = await getattr(injector_module.TildaMetaInjector(Forbidden()), method)('123', value)
    assert result['status'] == 'write_unsupported' and result['receipt'] is None
    assert result['upstream_mutation_calls'] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize('mock_mode', [True, False])
async def test_api_leaf_cannot_write_even_in_mock_mode(mock_mode):
    reader = Reader()
    result = await make_client(reader, mock_mode=mock_mode).update_page('123', {'title': 'New'})
    assert result['status'] == 'write_unsupported' and not reader.calls


@pytest.mark.asyncio
async def test_get_protocol_and_json_error_are_checked_independently_of_http200():
    reader = Reader()
    assert (await make_client(reader).validate_credentials())['ready'] is True
    query = parse_qs(urlsplit(reader.calls[0]).query)
    assert urlsplit(reader.calls[0]).path == '/v1/getprojectslist/'
    assert query == {'publickey': ['fixture-public'], 'secretkey': ['fixture-secret']}
    for body in (b'{"status":"ERROR","message":"invalid secretkey"}', b'{}', b'not-json'):
        with pytest.raises(client_module.TildaAPIError):
            await make_client(Reader(body)).validate_credentials()


@pytest.mark.asyncio
@pytest.mark.parametrize('body,status,code', [
    (b'{}', 429, 'tilda_quota_exceeded'), (b'{}', 403, 'tilda_credentials_rejected'),
    (b'{}', 500, 'tilda_upstream_unavailable'),
    (b'{"status":"ERROR","message":"150 requests limit"}', 200, 'tilda_quota_exceeded'),
])
async def test_stable_source_errors_never_expose_credential_urls(body, status, code):
    with pytest.raises(client_module.TildaAPIError) as error:
        await make_client(Reader(body, status)).validate_credentials()
    assert str(error.value) == code and 'fixture-secret' not in str(error.value)


@pytest.mark.asyncio
async def test_page_scope_empty_and_null_are_preserved_and_wrong_project_is_rejected():
    reader = Reader(b'{"status":"FOUND","result":{"id":"123","projectid":"42","title":"","descr":null}}')
    result = await make_client(reader).get_page('123', '42')
    assert result['title'] == '' and result['descr'] is None and 'h1' not in result
    with pytest.raises(client_module.TildaAPIError, match='tilda_scope_mismatch'):
        await make_client(reader).get_page('123', '43')


@pytest.mark.asyncio
async def test_no_mock_read_no_arbitrary_base_url_and_no_secret_in_transport_error():
    with pytest.raises(client_module.TildaAPIError, match='tilda_read_disabled'):
        await make_client(mock_mode=True).validate_credentials()
    with pytest.raises(client_module.TildaAPIError, match='tilda_base_url_unsupported'):
        make_client(base_url='http://127.0.0.1/v1')
    with pytest.raises(client_module.TildaAPIError) as error:
        await make_client(Reader(error=True)).validate_credentials()
    assert 'fixture-secret' not in str(error.value) and error.value.__suppress_context__


@pytest.mark.parametrize('path', ['meta', 'schema', 'interlinks'])
def test_authenticated_legacy_routes_cannot_wrap_unsupported_as_ok(path):
    app = FastAPI(); app.include_router(routes.router)
    with TestClient(app) as client:
        data = {'project_id': 'p', 'page_id': '123', 'changes': [{'op': 'replace', 'path': '/title', 'value': None}]}
        assert client.post('/internal/tilda/' + path, json=data).status_code == 401
        result = client.post('/internal/tilda/' + path, json=data, headers={'X-Internal-API-Key': 'i' * 32})
        assert result.status_code == 200 and result.json()['status'] == 'write_unsupported'


def hint():
    return {'binding': '11111111-1111-4111-8111-111111111111', 'token': 'a' * 64,
            'projectid': '42', 'pageid': '123', 'published': '1790000000', 'publickey': 'fixture-public'}


def test_official_get_ack_only_after_durable_acceptance_and_no_fetch(monkeypatch):
    app = FastAPI(); app.include_router(webhooks.router)
    sent = []
    original = httpx.AsyncClient
    def respond(request):
        sent.append(request)
        return httpx.Response(200, json={'status': 'queued', 'job_id': 'job-1'})
    monkeypatch.setattr(webhooks.httpx, 'AsyncClient', lambda **kwargs: original(transport=httpx.MockTransport(respond), **kwargs))
    with TestClient(app) as client:
        params = hint()
        callback = '/webhook/' + params.pop('binding') + '/' + params.pop('token') + '/published'
        response = client.get(callback, params=params)
        assert response.status_code == 200 and response.text == 'ok'
        assert len(sent) == 1 and sent[0].method == 'POST' and '/webhook/published' in sent[0].url.path
        assert client.post('/webhook/published', json={}).status_code == 410


def test_access_logger_drops_callback_query_but_keeps_other_access_records():
    record = logging.LogRecord('uvicorn.access', logging.INFO, '', 0, '%s %s %s %s %s',
        ('client', 'GET', '/api/tilda/webhook/published?token=private&publickey=hidden', '1.1', 200), None)
    assert CallbackAccessFilter().filter(record)
    assert 'private' not in record.getMessage() and 'hidden' not in record.getMessage()


def test_transport_debug_logs_redact_credential_queries_and_callback_capabilities():
    for message in ('GET https://api.tildacdn.info/v1/getpage/?publickey=private&secretkey=hidden',
                    '/api/tilda/webhook/project/capability/published?pageid=123'):
        record = logging.LogRecord('httpcore.http11', logging.DEBUG, '', 0, '%s', (message,), None)
        assert TildaTransportFilter().filter(record)
        assert all(secret not in record.getMessage() for secret in ('private', 'hidden', 'capability'))
