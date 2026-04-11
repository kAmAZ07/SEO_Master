import asyncio
import json

import pytest

pytest.importorskip("aio_pika")
pytest.importorskip("sqlalchemy")


def test_audit_task_created_handler_queues_full_audit(monkeypatch):
    async def _run():
        from services.audit_service.events import task_created_handler as handler
        from services.audit_service.schemas.audit import AuditQueuedResponse

        calls = []

        async def fake_queue_full_audit_for_task_created(**kwargs):
            calls.append(kwargs)
            return AuditQueuedResponse(
                audit_id="task-task-1",
                status="queued",
                mode="full",
                project_id=kwargs["project_id"],
            )

        monkeypatch.setattr(
            "services.audit_service.main.queue_full_audit_for_task_created",
            fake_queue_full_audit_for_task_created,
        )

        event = {
            "event_name": "TaskCreated",
            "payload": {
                "task_id": "task-1",
                "project_id": "project-1",
                "task_type": "ADD_INTERNAL_LINKS",
                "url": "https://example.com/page",
                "metadata": {"platform": "wordpress", "seed_urls": ["https://example.com/seed"]},
                "correlation_id": "corr-1",
            },
        }

        await handler._handle_message(json.dumps(event).encode("utf-8"))

        assert len(calls) == 1
        assert calls[0]["task_id"] == "task-1"
        assert calls[0]["project_id"] == "project-1"
        assert calls[0]["root_url"] == "https://example.com/page"
        assert calls[0]["task_type"] == "ADD_INTERNAL_LINKS"
        assert calls[0]["metadata"]["platform"] == "wordpress"
        assert calls[0]["correlation_id"] == "corr-1"

    asyncio.run(_run())


def test_queue_full_audit_for_task_created_reuses_existing_audit(monkeypatch):
    async def _run():
        from services.audit_service import main

        class ExistingAudit:
            audit_id = "task-task-1"
            status = "running"
            mode = "full"
            project_id = "project-1"

        async def fake_get_audit_row(audit_id: str):
            assert audit_id == "task-task-1"
            return ExistingAudit()

        async def fake_enqueue_audit(**kwargs):
            raise AssertionError("enqueue should not be called for an existing audit")

        monkeypatch.setattr(main, "_get_audit_row", fake_get_audit_row)
        monkeypatch.setattr(main, "_enqueue_audit", fake_enqueue_audit)

        result = await main.queue_full_audit_for_task_created(
            task_id="task-1",
            project_id="project-1",
            root_url="https://example.com/page",
            task_type="ADD_INTERNAL_LINKS",
            metadata={"platform": "wordpress"},
            correlation_id="corr-1",
        )

        assert result.audit_id == "task-task-1"
        assert result.status == "running"
        assert result.mode == "full"
        assert result.project_id == "project-1"

    asyncio.run(_run())
