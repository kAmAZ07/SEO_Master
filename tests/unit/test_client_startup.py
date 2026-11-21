"""Client startup must never rewrite schema or plaintext credentials."""
from contextlib import nullcontext
from unittest.mock import Mock
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from services.client_api_gateway import db
from services.client_api_gateway.models import Base


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
