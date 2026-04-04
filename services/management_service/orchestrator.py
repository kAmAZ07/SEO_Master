import asyncio
import uuid
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, Optional

import httpx
from sqlalchemy.orm import Session
from tenacity import retry, stop_after_attempt, wait_exponential

from config.logging_config import get_logger
from services.management_service.config import settings
from services.management_service.client_api_adapter import deploy_changes as deploy_client_changes
from services.management_service.db.models import HITLApproval, HITLStatus, Task, TaskStatus
from services.management_service.events.publishers import publish_event

logger = get_logger(__name__)


class SagaState(str, Enum):
    INITIATED = "initiated"
    CRAWLING = "crawling"
    CRAWL_COMPLETED = "crawl_completed"
    CALCULATING_SCORES = "calculating_scores"
    SCORES_COMPLETED = "scores_completed"
    GENERATING_CONTENT = "generating_content"
    CONTENT_GENERATED = "content_generated"
    AWAITING_HITL = "awaiting_hitl"
    HITL_APPROVED = "hitl_approved"
    HITL_REJECTED = "hitl_rejected"
    APPLYING_CHANGES = "applying_changes"
    COMPLETED = "completed"
    FAILED = "failed"
    COMPENSATING = "compensating"


class OptimizationSaga:
    def __init__(self, project_id: str, url: str, task_id: Optional[str] = None):
        self.project_id = project_id
        self.url = url
        self.task_id = task_id
        self.saga_id = str(uuid.uuid4())
        self.state = SagaState.INITIATED
        self.context: Dict[str, Any] = {}
        self.correlation_id = str(uuid.uuid4())
        self.client: Optional[httpx.AsyncClient] = None

    async def execute(self) -> bool:
        from services.management_service.db.session import SessionLocal

        db = SessionLocal()
        try:
            await self._save_saga_state(db)

            async with httpx.AsyncClient(timeout=settings.SERVICE_REQUEST_TIMEOUT) as client:
                self.client = client

                await self._run_crawl()
                await self._wait_for_crawl_completion()
                await self._save_saga_state(db)

                await self._trigger_scores_calculation()
                await self._wait_for_scores_completion()
                await self._save_saga_state(db)

                await self._trigger_content_generation()
                await self._wait_for_content_generation()
                await self._save_saga_state(db)

                if self.task_id:
                    hitl_decision = await self._create_hitl_decision(db)
                    approved = await self._wait_for_hitl_decision(db, str(hitl_decision.id))
                else:
                    self.state = SagaState.HITL_APPROVED
                    approved = True

                if not approved:
                    self.state = SagaState.HITL_REJECTED
                    await self._update_task_status(db, TaskStatus.CANCELLED)
                    await self._publish_completion_event(success=False, reason="HITL rejected")
                    return False

                await self._apply_changes()
                await self._wait_for_changes_applied()

                self.state = SagaState.COMPLETED
                await self._update_task_status(db, TaskStatus.COMPLETED)
                await self._save_saga_state(db)
                await self._publish_completion_event(success=True)
                return True
        except Exception as exc:
            logger.error(
                f"Saga failed for project {self.project_id}, url {self.url}: {exc}",
                extra={"correlation_id": self.correlation_id, "saga_id": self.saga_id},
                exc_info=True,
            )
            self.state = SagaState.FAILED
            await self._compensate()
            await self._update_task_status(db, TaskStatus.FAILED)
            await self._save_saga_state(db)
            await self._publish_completion_event(success=False, reason=str(exc))
            return False
        finally:
            db.close()

    @retry(
        stop=stop_after_attempt(settings.SAGA_RETRY_MAX_ATTEMPTS),
        wait=wait_exponential(multiplier=1, min=2, max=10),
    )
    async def _run_crawl(self):
        self.state = SagaState.CRAWLING

        response = await self.client.post(
            f"{settings.AUDIT_SERVICE_URL}/audit/public",
            json={
                "root_url": self.url,
                "site_type_hint": "unknown",
                "platform": "generic",
                "options": {"max_pages": 25, "max_depth": 2, "js_render": False},
            },
        )
        response.raise_for_status()

        result = response.json()
        crawl_id = result.get("audit_id")
        if not crawl_id:
            raise RuntimeError("Audit service did not return audit_id")

        self.context["crawl_id"] = crawl_id
        logger.info(
            "Crawl initiated",
            extra={
                "crawl_id": crawl_id,
                "correlation_id": self.correlation_id,
                "saga_id": self.saga_id,
            },
        )

    async def _wait_for_crawl_completion(self):
        timeout = datetime.utcnow() + timedelta(minutes=settings.SAGA_TIMEOUT_MINUTES)

        while datetime.utcnow() < timeout:
            response = await self.client.get(
                f"{settings.AUDIT_SERVICE_URL}/audit/{self.context['crawl_id']}",
            )

            if response.status_code == 404:
                await asyncio.sleep(2)
                continue

            response.raise_for_status()
            result = response.json()
            status = result.get("status")

            if status == "completed":
                self.state = SagaState.CRAWL_COMPLETED
                self.context["crawl_result"] = result
                return

            if status == "failed":
                raise RuntimeError(f"Crawl failed: {self.context['crawl_id']}")

            await asyncio.sleep(2)

        raise TimeoutError("Crawl timeout")

    @retry(
        stop=stop_after_attempt(settings.SAGA_RETRY_MAX_ATTEMPTS),
        wait=wait_exponential(multiplier=1, min=2, max=10),
    )
    async def _trigger_scores_calculation(self):
        self.state = SagaState.CALCULATING_SCORES

        crawl_result = self.context.get("crawl_result", {})
        pages = crawl_result.get("pages", []) if isinstance(crawl_result, dict) else []
        findings = crawl_result.get("findings", []) if isinstance(crawl_result, dict) else []

        content_parts = []
        for page in pages[:10]:
            if isinstance(page, dict):
                content_parts.append(str(page.get("title") or ""))
                content_parts.append(str(page.get("description") or ""))
        for finding in findings[:20]:
            if isinstance(finding, dict):
                content_parts.append(str(finding.get("message") or finding.get("title") or ""))

        content_text = "\n".join(part for part in content_parts if part).strip()

        ffscore_response = await self.client.post(
            f"{settings.SEMANTIC_SERVICE_URL}/semantic/ff-score",
            json={
                "project_id": self.project_id,
                "root_url": self.url,
                "content_text": content_text,
            },
        )
        ffscore_response.raise_for_status()
        ff_data = ffscore_response.json()

        self.context["ffscore"] = ff_data.get("ff_score")
        self.context["eeat_score"] = (ff_data.get("eeat") or {}).get("score")
        self.context["ffscore_task_id"] = ff_data.get("ff_score_id")
        self.context["eeat_task_id"] = ff_data.get("eeat_score_id")

    async def _wait_for_scores_completion(self):
        if self.context.get("ffscore") is None:
            raise RuntimeError("FF-Score calculation returned no score")

        self.state = SagaState.SCORES_COMPLETED

    @retry(
        stop=stop_after_attempt(settings.SAGA_RETRY_MAX_ATTEMPTS),
        wait=wait_exponential(multiplier=1, min=2, max=10),
    )
    async def _trigger_content_generation(self):
        self.state = SagaState.GENERATING_CONTENT

        crawl_result = self.context.get("crawl_result", {})
        pages = crawl_result.get("pages", []) if isinstance(crawl_result, dict) else []
        snippets = []
        for page in pages[:10]:
            if isinstance(page, dict):
                snippets.append(str(page.get("title") or ""))
                snippets.append(str(page.get("description") or ""))

        response = await self.client.post(
            f"{settings.SEMANTIC_SERVICE_URL}/semantic/drafts",
            json={
                "project_id": self.project_id,
                "root_url": self.url,
                "content": "\n".join(snippets),
            },
        )
        response.raise_for_status()

        result = response.json()
        self.context["content_generation_id"] = result.get("draft_id")
        self.context["generated_content"] = result.get("drafts", {})

    async def _wait_for_content_generation(self):
        if self.context.get("generated_content") is None:
            raise RuntimeError("Content generation returned empty response")

        self.state = SagaState.CONTENT_GENERATED

    async def _create_hitl_decision(self, db: Session) -> HITLApproval:
        self.state = SagaState.AWAITING_HITL

        if not self.task_id:
            raise ValueError("task_id is required to create HITL approval record")

        crawl_data = self.context.get("crawl_result", {})
        old_content = {
            "title": crawl_data.get("title"),
            "description": crawl_data.get("description"),
            "h1": crawl_data.get("h1"),
            "schema_org": crawl_data.get("schema_org"),
        }

        new_content = self.context.get("generated_content", {})

        decision = HITLApproval(
            task_id=self.task_id,
            project_id=self.project_id,
            status=HITLStatus.PENDING,
            diff_data={"before": old_content, "after": new_content},
            impact_score=self.context.get("ffscore"),
            recommendation="Review generated SEO changes before deployment",
            metadata={
                "saga_id": self.saga_id,
                "correlation_id": self.correlation_id,
                "url": self.url,
                "ffscore": self.context.get("ffscore"),
                "eeat_score": self.context.get("eeat_score"),
            },
        )
        # Backward-compatible fields for existing tests and old integrations.
        decision.old_content = old_content
        decision.new_content = new_content

        db.add(decision)
        db.commit()
        db.refresh(decision)

        await publish_event(
            routing_key="management.hitl.approval_required",
            payload={
                "payload": {
                    "decision_id": str(decision.id),
                    "project_id": self.project_id,
                    "url": self.url,
                },
                "decision_id": str(decision.id),
                "correlation_id": self.correlation_id,
            },
            event_type="HITLApprovalRequired",
            correlation_id=self.correlation_id,
        )

        return decision

    async def _wait_for_hitl_decision(self, db: Session, decision_id: str) -> bool:
        timeout = datetime.utcnow() + timedelta(hours=settings.HITL_TIMEOUT_HOURS)

        while datetime.utcnow() < timeout:
            decision = db.query(HITLApproval).filter(HITLApproval.id == decision_id).first()
            if decision is None:
                await asyncio.sleep(2)
                continue

            if decision.status == HITLStatus.APPROVED:
                self.state = SagaState.HITL_APPROVED
                return True
            if decision.status == HITLStatus.REJECTED:
                self.state = SagaState.HITL_REJECTED
                return False

            await asyncio.sleep(2)

        raise TimeoutError("HITL decision timeout")

    @retry(
        stop=stop_after_attempt(settings.SAGA_RETRY_MAX_ATTEMPTS),
        wait=wait_exponential(multiplier=1, min=2, max=10),
    )
    async def _apply_changes(self):
        self.state = SagaState.APPLYING_CHANGES

        generated = self.context.get("generated_content") or {}
        if isinstance(generated, dict):
            after = {
                "title": generated.get("title") or generated.get("meta_title"),
                "description": generated.get("description") or generated.get("meta_description"),
                "h1": generated.get("h1"),
            }
        else:
            after = {"content": generated}

        payload = {
            "project_id": self.project_id,
            "task_id": self.task_id,
            "change_type": "meta",
            "entity_id": self.url,
            "entity_type": "web_page",
            "changes": {"before": {}, "after": after},
            "metadata": {
                "saga_id": self.saga_id,
                "correlation_id": self.correlation_id,
            },
        }

        deploy_task_id = self.task_id or self.saga_id
        result = await deploy_client_changes(
            task_id=deploy_task_id,
            changes_data=payload,
            correlation_id=self.correlation_id,
            use_internal=None,
        )

        self.context["change_id"] = result.get("change_id") or result.get("deployment_id")

    async def _wait_for_changes_applied(self):
        change_id = self.context.get("change_id")
        if not change_id:
            raise RuntimeError("Change ID missing after deploy")

        timeout = datetime.utcnow() + timedelta(minutes=settings.SAGA_TIMEOUT_MINUTES)

        while datetime.utcnow() < timeout:
            response = await self.client.get(
                f"{settings.CLIENT_GATEWAY_URL}/changes/pending/{self.project_id}",
                headers={
                    "X-Internal-API-Key": settings.INTERNAL_API_KEY,
                    "X-Correlation-ID": self.correlation_id,
                },
            )
            response.raise_for_status()
            rows = response.json() or []

            for row in rows:
                if str(row.get("change_id")) != str(change_id):
                    continue

                row_status = str(row.get("status") or "").lower()
                if row_status in {"applied", "received", "pending"}:
                    return
                if row_status in {"failed", "rejected"}:
                    raise RuntimeError("Changes application failed")

            # If row already consumed by downstream worker and no longer pending, accept as applied.
            if not rows:
                return

            await asyncio.sleep(2)

        raise TimeoutError("Changes application timeout")

    async def _compensate(self):
        self.state = SagaState.COMPENSATING
        logger.info(
            "Starting compensation for saga",
            extra={
                "correlation_id": self.correlation_id,
                "saga_id": self.saga_id,
                "project_id": self.project_id,
                "url": self.url,
            },
        )

        if "change_id" in self.context:
            try:
                async with httpx.AsyncClient(timeout=settings.SERVICE_REQUEST_TIMEOUT) as client:
                    response = await client.post(
                        f"{settings.CLIENT_GATEWAY_URL}/internal/deploy/{self.context['change_id']}/rollback",
                        headers={
                            "X-Internal-API-Key": settings.INTERNAL_API_KEY,
                            "X-Correlation-ID": self.correlation_id,
                        },
                    )
                    response.raise_for_status()
                    self.context["rollback"] = response.json()
            except Exception as exc:
                logger.warning(
                    "Rollback request failed; manual compensation may still be required",
                    extra={
                        "change_id": self.context.get("change_id"),
                        "project_id": self.project_id,
                        "correlation_id": self.correlation_id,
                        "error": str(exc),
                    },
                )

    async def _update_task_status(self, db: Session, status: TaskStatus):
        if not self.task_id:
            return

        task = db.query(Task).filter(Task.id == self.task_id).first()
        if task:
            task.status = status
            task.updated_at = datetime.utcnow()
            db.commit()

    async def _save_saga_state(self, db: Session):
        if not self.task_id:
            return

        task = db.query(Task).filter(Task.id == self.task_id).first()
        if not task:
            return

        metadata = task.metadata or {}
        metadata["saga_state"] = self.state.value
        metadata["saga_id"] = self.saga_id
        metadata["saga_correlation_id"] = self.correlation_id
        metadata["saga_context"] = self.context
        task.metadata = metadata
        task.updated_at = datetime.utcnow()
        db.commit()

    async def _publish_completion_event(self, success: bool, reason: Optional[str] = None):
        event_type = "OptimizationCompleted" if success else "OptimizationFailed"
        routing_suffix = "completed" if success else "failed"

        await publish_event(
            routing_key=f"management.optimization.{routing_suffix}",
            payload={
                "payload": {
                    "saga_id": self.saga_id,
                    "project_id": self.project_id,
                    "url": self.url,
                    "task_id": self.task_id,
                    "success": success,
                    "reason": reason,
                    "ffscore": self.context.get("ffscore"),
                    "eeat_score": self.context.get("eeat_score"),
                },
                "saga_id": self.saga_id,
                "correlation_id": self.correlation_id,
            },
            event_type=event_type,
            correlation_id=self.correlation_id,
        )


async def run_optimization_cycle(project_id: str, url: str, task_id: Optional[str] = None) -> bool:
    saga = OptimizationSaga(project_id, url, task_id)
    return await saga.execute()
