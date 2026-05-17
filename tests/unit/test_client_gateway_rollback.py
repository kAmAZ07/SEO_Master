from types import SimpleNamespace
from uuid import uuid4

import pytest

from services.client_api_gateway import deployment_dispatcher as dispatcher


def test_normalize_changes_builds_reversible_patch_ops():
    changes = {
        "before": {
            "title": "Old title",
            "description": "Old description",
        },
        "after": {
            "title": "New title",
        },
    }

    ops = dispatcher._normalize_changes("meta", changes)

    assert ops == [
        {
            "op": "replace",
            "path": "/title",
            "value": "New title",
            "old_value": "Old title",
        },
        {
            "op": "remove",
            "path": "/description",
            "old_value": "Old description",
        },
    ]


def test_build_reverse_patch_ops_restores_previous_state_in_reverse_order():
    ops = [
        {"op": "add", "path": "/title", "value": "New title"},
        {
            "op": "replace",
            "path": "/description",
            "value": "New description",
            "old_value": "Old description",
        },
        {"op": "remove", "path": "/h1", "old_value": "Old H1"},
    ]

    assert dispatcher._build_reverse_patch_ops(ops) == [
        {"op": "add", "path": "/h1", "value": "Old H1"},
        {"op": "replace", "path": "/description", "value": "Old description"},
        {"op": "remove", "path": "/title"},
    ]


def test_target_platform_uses_metadata_then_entity_type():
    assert dispatcher._target_platform("web_page", {"platform": "wordpress"}) == "wordpress"
    assert dispatcher._target_platform("web_page", {"target_platform": "tilda"}) == "tilda"
    assert dispatcher._target_platform("tilda_page", {}) == "tilda"
    assert dispatcher._target_platform("web_page", {}) == "wordpress"


@pytest.mark.asyncio
async def test_rollback_change_dispatches_saved_rollback_changes(monkeypatch):
    deployment_id = uuid4()
    calls = []

    async def fake_dispatch_change(**kwargs):
        calls.append(kwargs)
        return {"status": "applied", "changes": kwargs["changes"]}

    monkeypatch.setattr(dispatcher, "dispatch_change", fake_dispatch_change)

    deployment_log = SimpleNamespace(
        id=deployment_id,
        project_id="project-1",
        entity_id="page-1",
        entity_type="web_page",
        change_type="meta",
        changes=[
            {
                "op": "replace",
                "path": "/title",
                "value": "New title",
                "old_value": "Old title",
            }
        ],
        meta={
            "platform": "wordpress",
            "rollback_changes": [
                {"op": "replace", "path": "/title", "value": "Old title"}
            ],
        },
    )

    result = await dispatcher.rollback_change(
        db=object(),
        deployment_log=deployment_log,
        correlation_id="corr-1",
    )

    assert result["status"] == "applied"
    assert calls[0]["changes"] == [
        {"op": "replace", "path": "/title", "value": "Old title"}
    ]
    assert calls[0]["metadata"]["rollback_of"] == str(deployment_id)
    assert calls[0]["metadata"]["rollback_changes"] == []
    assert calls[0]["correlation_id"] == "corr-1"
