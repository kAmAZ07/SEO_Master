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

    def _build_full_audit_payload(self) -> dict:
        return {
            "project_id": self.project_id,
            "root_url": self.url,
            "site_type_hint": "unknown",
            "platform": "generic",
        }

    @staticmethod
    def _build_content_text(crawl_result: dict) -> str:
        pages = crawl_result.get("pages", []) if isinstance(crawl_result, dict) else []
        findings = crawl_result.get("findings", []) if isinstance(crawl_result, dict) else []

        content_parts = []
        for page in pages[:10]:
            if not isinstance(page, dict):
                continue
            content_parts.append(str(page.get("title") or ""))
            content_parts.append(str(page.get("description") or ""))
            content_parts.append(str(page.get("h1") or ""))
        for finding in findings[:20]:
            if not isinstance(finding, dict):
                continue
            content_parts.append(str(finding.get("message") or finding.get("title") or finding.get("description") or ""))

        return "\n".join(part for part in content_parts if part).strip()

    def _extract_current_page_snapshot(self, crawl_result: dict) -> dict:
        if not isinstance(crawl_result, dict):
            return {"title": None, "description": None, "h1": None, "schema_org": None}

        pages = crawl_result.get("pages", [])
        selected_page = None
        for page in pages:
            if not isinstance(page, dict):
                continue
            if str(page.get("url") or "").rstrip("/") == self.url.rstrip("/"):
                selected_page = page
                break
        if selected_page is None and pages:
            first_page = pages[0]
            selected_page = first_page if isinstance(first_page, dict) else None

        # Older tests and some integrations still pass a flattened crawl snapshot
        # instead of a page list. Keep supporting that shape to avoid regressions.
        selected_page = selected_page or crawl_result
        return {
            "title": selected_page.get("title"),
            "description": selected_page.get("description"),
            "h1": selected_page.get("h1"),
            "schema_org": selected_page.get("schema_org"),
        }

    @staticmethod
    def _derive_backlinks_count(crawl_result: dict, reporting_summary: dict | None) -> int:
        reporting_signals = (reporting_summary or {}).get("signals") or {}
        reporting_backlinks = reporting_signals.get("backlinks_count")
        if isinstance(reporting_backlinks, (int, float)):
            return max(0, int(reporting_backlinks))

        summary = crawl_result.get("summary") if isinstance(crawl_result, dict) else {}
        backlinks = summary.get("backlinks") if isinstance(summary, dict) else {}
        top_sites = backlinks.get("top_linking_sites") if isinstance(backlinks, dict) else []
        if isinstance(top_sites, list):
            return len(top_sites)
        return 0

    @staticmethod
    def _derive_brand_mentions(reporting_summary: dict | None) -> int:
        signals = (reporting_summary or {}).get("signals") or {}
        brand_mentions = signals.get("brand_mentions")
        if isinstance(brand_mentions, (int, float)):
            return max(0, int(brand_mentions))
        return 0

    async def _fetch_reporting_summary(self) -> dict:
        try:
            response = await self.client.get(
                f"{settings.REPORTING_SERVICE_URL}/reporting/projects/{self.project_id}/summary",
                params={"root_url": self.url},
            )
            response.raise_for_status()
            return response.json()
        except Exception as exc:
            logger.warning(
                "Failed to fetch reporting summary for saga",
                extra={
                    "project_id": self.project_id,
                    "url": self.url,
                    "correlation_id": self.correlation_id,
                    "error": str(exc),
                },
            )
            return {}

    async def _run_semantic_analysis(self, crawl_result: dict, content_text: str) -> dict:
        payload = {
            "project_id": self.project_id,
            "root_url": self.url,
            "audit_id": crawl_result.get("audit_id") if isinstance(crawl_result, dict) else None,
            "mode": crawl_result.get("mode") if isinstance(crawl_result, dict) else None,
            "content_text": content_text,
            "pages": crawl_result.get("pages", []) if isinstance(crawl_result, dict) else [],
            "keywords": crawl_result.get("keywords", []) if isinstance(crawl_result, dict) else [],
            "serp_top10_texts": crawl_result.get("serp_top10_texts", []) if isinstance(crawl_result, dict) else [],
        }
        response = await self.client.post(f"{settings.SEMANTIC_SERVICE_URL}/semantic/analyze", json=payload)
        response.raise_for_status()
        return response.json()

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
            f"{settings.AUDIT_SERVICE_URL}/audit/full",
            json=self._build_full_audit_payload(),
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
        content_text = self._build_content_text(crawl_result)
        reporting_summary = await self._fetch_reporting_summary()
        semantic_analysis = await self._run_semantic_analysis(crawl_result, content_text)

        self.context["reporting_summary"] = reporting_summary
        self.context["semantic_analysis"] = semantic_analysis

        audit_summary = crawl_result.get("summary", {}) if isinstance(crawl_result, dict) else {}
        audit_findings = crawl_result.get("findings", []) if isinstance(crawl_result, dict) else []
        reporting_signals = (reporting_summary.get("signals") or {}) if isinstance(reporting_summary, dict) else {}
        reporting_sources = (reporting_summary.get("sources") or {}) if isinstance(reporting_summary, dict) else {}
        semantic_inputs = semantic_analysis.get("inputs") if isinstance(semantic_analysis, dict) else {}
        semantic_distance = (semantic_analysis.get("semantic_distance") or {}).get("semantic_distance") if isinstance(semantic_analysis, dict) else None
        keyword_coverage = (semantic_analysis.get("keyword_coverage") or {}).get("coverage") if isinstance(semantic_analysis, dict) else None

        semantic_unavailable = []
        if isinstance(semantic_inputs, dict):
            semantic_unavailable = semantic_inputs.get("unavailable") or []
        semantic_distance_source = "semantic_analysis"
        keyword_coverage_source = "semantic_analysis"
        if "serp_top10_texts" in semantic_unavailable:
            semantic_distance_source = "semantic_analysis_degraded"
        if "keywords" in semantic_unavailable:
            keyword_coverage_source = "semantic_analysis_degraded"

        backlinks_count = self._derive_backlinks_count(crawl_result, reporting_summary)
        brand_mentions = self._derive_brand_mentions(reporting_summary)

        ffscore_response = await self.client.post(
            f"{settings.SEMANTIC_SERVICE_URL}/semantic/ff-score",
            json={
                "project_id": self.project_id,
                "root_url": self.url,
                "content_text": content_text,
                "audit_summary": audit_summary,
                "audit_findings": audit_findings,
                "freshness_days_since_update": reporting_signals.get("freshness_days_since_update"),
                "serp_shift": reporting_signals.get("serp_shift"),
                "link_velocity": reporting_signals.get("link_velocity"),
                "semantic_distance": semantic_distance,
                "keyword_coverage": keyword_coverage,
                "backlinks_count": backlinks_count,
                "brand_mentions": brand_mentions,
                "input_sources": {
                    "freshness_days_since_update": reporting_sources.get("freshness_days_since_update"),
                    "serp_shift": reporting_sources.get("serp_shift"),
                    "link_velocity": reporting_sources.get("link_velocity"),
                    "semantic_distance": semantic_distance_source,
                    "keyword_coverage": keyword_coverage_source,
                    "backlinks_count": "audit_summary_backlinks" if backlinks_count else None,
                    "brand_mentions": reporting_sources.get("brand_mentions"),
                },
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
        old_content = self._extract_current_page_snapshot(crawl_data)

        new_content = self.context.get("generated_content", {})

        decision = HITLApproval(
            task_id=self.task_id,
            project_id=self.project_id,
            status=HITLStatus.PENDING,
            diff_data={"before": old_content, "after": new_content},
            impact_score=self.context.get("ffscore"),
            recommendation="Review generated SEO changes before deployment",
            meta={
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

        metadata = task.meta or {}
        metadata["saga_state"] = self.state.value
        metadata["saga_id"] = self.saga_id
        metadata["saga_correlation_id"] = self.correlation_id
        metadata["saga_context"] = self.context
        task.meta = metadata
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



