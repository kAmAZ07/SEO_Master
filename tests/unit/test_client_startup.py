"""Client startup must never rewrite schema or plaintext credentials."""
from contextlib import nullcontext
from unittest.mock import Mock
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from services.client_api_gateway import db
from services.client_api_gateway.models import Base


def test_retired_http_key_management_never_accesses_the_key_store(monkeypatch):
    from services.client_api_gateway import main

    secret = 'i' * 32
    monkeypatch.setattr(main.settings, 'INTERNAL_API_KEY', secret)
    # A dependency that fails before any database or key configuration can be read.
    store = Mock(side_effect=AssertionError('retired key endpoint accessed DB'))
    monkeypatch.setitem(main.app.dependency_overrides, db.get_db, lambda: store())
    client = TestClient(main.app)
    try:
        prefix = '/api/client/keys/' + str(uuid4())
        for method, path in [('GET', prefix), ('POST', prefix + '/rotate')]:
            response = client.request(method, path, headers={'X-Internal-API-Key': secret})
            assert response.status_code == 410
            assert response.json() == {'detail': 'client_keys_managed_by_environment'}
    finally:
        client.close()
    store.assert_not_called()


@pytest.mark.parametrize('kind', ['missing', 'shared', 'executor', 'wrong', 'reused', 'unconfigured'])
def test_execution_routes_reject_wrong_service_authority_before_executor(monkeypatch, kind):
    from services.client_api_gateway import main

    shared, executor, execution = ('k' * 32, 'm' * 32, 'e' * 32)
    monkeypatch.setattr(main.settings, 'INTERNAL_API_KEY', shared)
    monkeypatch.setattr(main.settings, 'MANAGEMENT_EXECUTOR_API_KEY', executor)
    monkeypatch.setattr(main.settings, 'CLIENT_EXECUTION_API_KEY',
                        shared if kind == 'reused' else '' if kind == 'unconfigured' else execution)
    factory = Mock(side_effect=AssertionError('executor reached with wrong service authority'))
    monkeypatch.setattr(main, 'HITLExecutor', factory)
    key = {'shared': shared, 'executor': executor, 'wrong': 'w' * 32, 'reused': shared}.get(kind)
    headers = {'X-Client-Execution-Key': key} if key else {}
    if kind == 'shared':
        headers['X-Internal-API-Key'] = shared
    op, task, approval, version = (str(uuid4()) for _ in range(4))
    references = dict(operation_id=op, task_id=task, approval_id=approval, recommendation_version_id=version, content_hash='a' * 64)
    client = TestClient(main.app)
    try:
        for method, path, body in (
            ('POST', '', references), ('GET', '/' + op, None), ('POST', '/' + op + '/cancel', None),
            ('POST', '/' + op + '/rollback', {'operation_id': str(uuid4()), 'reason': 'Restore verified before'}),
        ):
            response = client.request(method, '/internal/executions' + path, headers=headers, json=body)
            assert response.status_code == (503 if kind in {'reused', 'unconfigured'} else 401)
    finally:
        client.close()
    factory.assert_not_called()


def test_startup_uses_only_readiness_not_orm_or_legacy_secret_migration(monkeypatch):
    connection = Mock()
    engine = Mock()
    engine.connect.return_value = nullcontext(connection)
    monkeypatch.setattr(db, "engine", engine)
    ddl = Mock(side_effect=AssertionError("runtime DDL reached"))
    monkeypatch.setattr(Base.metadata, "create_all", ddl)
    # A new implementation resolves the readiness function in this namespace.
    check = Mock()
    monkeypatch.setattr(db, "require_client_schema", check, raising=False)
    db.init_db()
    ddl.assert_not_called()
    check.assert_called_once_with(connection)


def test_database_startup_error_is_safe_for_server_logging(monkeypatch):
    from services.client_api_gateway import main
    import asyncio
    import traceback

    secret = 'fixture-private-database-detail'
    log = Mock()
    monkeypatch.setattr(main, 'setup_logging', Mock())
    monkeypatch.setattr(main, 'logger', log)
    monkeypatch.setattr(main, 'init_db', Mock(side_effect=RuntimeError(secret)))
    with pytest.raises(RuntimeError) as failure:
        asyncio.run(main.startup_event())
    assert str(failure.value) == 'CLIENT_DATABASE_NOT_READY'
    assert secret not in ''.join(traceback.format_exception(failure.value))
    assert secret not in str(log.mock_calls)


def test_database_request_error_does_not_escape_to_server_logger(monkeypatch):
    from sqlalchemy.exc import OperationalError
    from services.client_api_gateway import main

    secret = 'fixture-private-database-detail'
    log = Mock()
    session_log = Mock()
    session = Mock()
    session.query.side_effect = OperationalError('SELECT fixture', {'value': secret}, RuntimeError(secret))
    monkeypatch.setattr(main, 'logger', log)
    monkeypatch.setattr(db, 'logger', session_log)
    monkeypatch.setattr(db, 'SessionLocal', lambda: session)
    monkeypatch.setattr(main.settings, 'INTERNAL_API_KEY', 'i' * 32)
    client = TestClient(main.app)
    try:
        response = client.get('/changes/pending/' + str(uuid4()),
            headers={'X-Internal-API-Key': 'i' * 32, 'X-Correlation-ID': 'fixture-request'})
    finally:
        client.close()
    assert response.status_code == 500
    assert response.json() == {'detail': 'Internal server error', 'correlation_id': 'fixture-request'}
    assert response.headers['x-correlation-id'] == 'fixture-request'
    assert secret not in str(log.mock_calls) + str(session_log.mock_calls)
    session.rollback.assert_called_once()
    session.close.assert_called_once()
