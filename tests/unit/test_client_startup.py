"""Client startup must never rewrite schema or plaintext credentials."""
from contextlib import nullcontext
from unittest.mock import Mock

from services.client_api_gateway import db
from services.client_api_gateway.models import Base


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
