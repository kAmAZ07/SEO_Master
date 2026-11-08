import asyncio
from copy import deepcopy
import uuid
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, Optional

import httpx
from sqlalchemy.orm import Session
from tenacity import retry, stop_after_attempt, wait_exponential

from config.logging_config import get_logger
from services.management_service.config import settings
from services.management_service.db.models import HITLApproval, HITLStatus, Task, TaskStatus
from services.management_service.events.publishers import publish_event
from services.management_service.score_contract import parse_score_response
from services.management_service.proposal_handoff import (
    ProposalHandoffError, audit_proposal_request, bound_request, proposal_result, version_diff,
)

logger = get_logger(__name__)


class SagaState(str, Enum):
    INITIATED = "initiated"
    CRAWLING = "crawling"
    CRAWL_COMPLETED = "crawl_completed"
    CALCULATING_SCORES = "calculating_scores"
    SCORES_COMPLETED = "scores_completed"
    GENERATING_CONTENT = "generating_content"
    CONTENT_GENERATED = "content_generated"
    NEEDS_INPUT = "needs_input"
    NO_CHANGE = "no_change"
    AWAITING_HITL = "awaiting_hitl"
    HITL_APPROVED = "hitl_approved"
    HITL_REJECTED = "hitl_rejected"
    APPLYING_CHANGES = "applying_changes"
    COMPLETED = "completed"
    FAILED = "failed"
    COMPENSATING = "compensating"


class OptimizationSaga:
    def __init__(self, project_id: str, url: str, task_id: Optional[str] = None,
                 proposal_request: Optional[dict] = None, proposal_operation: Optional[str] = None):
        self.project_id = project_id
        self.url = url
        self.task_id = task_id
        self.saga_id = str(uuid.uuid4())
        self.state = SagaState.INITIATED
        self.context: Dict[str, Any] = {}
        if proposal_operation is not None:
            self.context["proposal_operation"] = proposal_operation
        if proposal_request is not None:
            self.context["proposal_request"] = deepcopy(proposal_request)
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
        # Проверяем известные входы до открытия БД и запуска нового обхода.
        try:
            if not self.task_id:
                raise ProposalHandoffError("proposal_task_required")
            if "proposal_request" in self.context:
                bound_request(self.context["proposal_request"], project_id=self.project_id,
                              task_id=self.task_id, url=self.url)
        except ProposalHandoffError as exc:
            missing = exc.code in {"proposal_task_required", "proposal_evidence_required"}
            self.state = SagaState.NEEDS_INPUT if missing else SagaState.FAILED
            self.context["needs_input" if missing else "error"] = {"reason": exc.code}
            return False
        from services.management_service.db.session import SessionLocal

        db = SessionLocal()
        try:
            await self._save_saga_state(db)

            async with httpx.AsyncClient(timeout=settings.SERVICE_REQUEST_TIMEOUT) as client:
                self.client = client

                await self._run_crawl()
                await self._wait_for_crawl_completion()
                self._bind_audit_evidence()
                await self._save_saga_state(db)

                await self._trigger_scores_calculation()
                await self._wait_for_scores_completion()
                await self._save_saga_state(db)

                await self._trigger_content_generation()
                await self._wait_for_content_generation()
                await self._save_saga_state(db)

                await self._create_hitl_decision(db)
                # Подготовка завершается на awaiting_hitl; решение и отдельное
                # исполнение не запускаются автоматически из старой саги.
                await self._save_saga_state(db)
                return True
        except ProposalHandoffError as exc:
            if exc.code == "proposal_no_change":
                self.state = SagaState.NO_CHANGE
            elif exc.code in {"proposal_needs_input", "proposal_evidence_required",
                              "proposal_evidence_ambiguous", "single_proposal_required"}:
                self.state = SagaState.NEEDS_INPUT
                self.context.setdefault("needs_input", {"reason": exc.code})
            else:
                self.state = SagaState.FAILED
                self.context["error"] = {"reason": exc.code}
                await self._update_task_status(db, TaskStatus.FAILED)
            await self._save_saga_state(db)
            return False
        except Exception as exc:
            logger.error(
                f"Saga failed for project {self.project_id}, url {self.url}: {exc}",
                extra={"correlation_id": self.correlation_id, "saga_id": self.saga_id},
                exc_info=True,
            )
            self.state = SagaState.FAILED
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

            if status in {"partial", "cancelled", "expired"}:
                raise ProposalHandoffError("proposal_evidence_required")

            await asyncio.sleep(2)

        raise TimeoutError("Crawl timeout")

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
        result = parse_score_response(ffscore_response.json())

        self.context["score_result"] = result.model_dump(mode="json")
        self.context["ffscore"] = result.ff_score
        self.context["ffscore_status"] = result.status
        self.context["ffscore_formula_version"] = result.formula_version
        self.context["eeat_score"] = result.eeat["score"]
        self.context["eeat_status"] = result.eeat["status"]
        self.context["ffscore_task_id"] = result.ff_score_id
        self.context["eeat_task_id"] = result.eeat_score_id

    async def _wait_for_scores_completion(self):
        # insufficient_data — завершённый расчёт с отсутствующими входами,
        # а не failure. Проверяем весь ответ, чтобы не принять malformed null.
        parse_score_response(self.context.get("score_result"))
        self.state = SagaState.SCORES_COMPLETED

    def _bind_audit_evidence(self):
        request = audit_proposal_request(
            self.context.get("crawl_result", {}), project_id=self.project_id,
            task_id=self.task_id, url=self.url, requested=self.context.get("proposal_request"),
            operation=self.context.get("proposal_operation"),
        )
        self.context["proposal_request"] = request.model_dump(mode="json")

    async def _trigger_content_generation(self):
        self.state = SagaState.GENERATING_CONTENT
        request = bound_request(
            self.context.get("proposal_request"), project_id=self.project_id,
            task_id=self.task_id, url=self.url,
        )
        response = await self.client.post(
            f"{settings.SEMANTIC_SERVICE_URL}/semantic/proposals",
            json=request.model_dump(mode="json"),
        )
        response.raise_for_status()
        if len(response.content) > 1_048_576:
            raise ProposalHandoffError("proposal_response_too_large")
        result = proposal_result(response.json(), request)
        self.context["proposal_status"] = result.status
        if result.version is None:
            self.context["needs_input"] = result.needs_input or {"reason": "proposal_no_change"}
            raise ProposalHandoffError("proposal_" + result.status)
        self.context["recommendation_version"] = result.version.model_dump(mode="json")

    async def _wait_for_content_generation(self):
        self._bound_proposal()
        self.state = SagaState.CONTENT_GENERATED

    def _bound_proposal(self):
        request = bound_request(
            self.context.get("proposal_request"), project_id=self.project_id,
            task_id=self.task_id, url=self.url,
        )
        result = proposal_result({
            "project_id": request.project_id, "target_url": request.target_url,
            "items": [{"operation_type": request.requested_operations()[0].value,
                       "status": self.context.get("proposal_status"),
                       "version": self.context.get("recommendation_version")}],
        }, request)
        if result.version is None:
            raise ProposalHandoffError("proposal_version_required")
        return result.version

    async def _create_hitl_decision(self, db: Session) -> HITLApproval:
        self.state = SagaState.AWAITING_HITL

        if not self.task_id:
            raise ValueError("task_id is required to create HITL approval record")

        version = self._bound_proposal()
        diff = version_diff(version)

        decision = HITLApproval(
            task_id=self.task_id,
            project_id=self.project_id,
            status=HITLStatus.PENDING,
            diff_data=diff,
            impact_score=self.context.get("ffscore"),
            recommendation="Review generated SEO changes before deployment",
            meta={
                "saga_id": self.saga_id,
                "correlation_id": self.correlation_id,
                "url": self.url,
                "ffscore": self.context.get("ffscore"),
                "eeat_score": self.context.get("eeat_score"),
                "recommendation_version_id": version.version_id,
                "recommendation_content_hash": version.content_hash,
                "proposal_version": version.version,
                "execution_mode": version.execution_mode.value,
            },
        )
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
                    "recommendation_version_id": version.version_id,
                    "content_hash": version.content_hash,
                },
                "decision_id": str(decision.id),
                "correlation_id": self.correlation_id,
            },
            event_type="HITLApprovalRequired",
            correlation_id=self.correlation_id,
        )

        return decision

    async def _apply_changes(self):
        raise ProposalHandoffError("version_bound_executor_required")

    async def _wait_for_changes_applied(self):
        raise ProposalHandoffError("independent_verification_required")

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

        metadata = dict(task.meta or {})
        metadata["saga_state"] = self.state.value
        metadata["saga_id"] = self.saga_id
        metadata["saga_correlation_id"] = self.correlation_id
        metadata["saga_context"] = deepcopy(self.context)
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
                    "ffscore_status": self.context.get("ffscore_status"),
                    "ffscore_formula_version": self.context.get("ffscore_formula_version"),
                    "eeat_score": self.context.get("eeat_score"),
                    "eeat_status": self.context.get("eeat_status"),
                },
                "saga_id": self.saga_id,
                "correlation_id": self.correlation_id,
            },
            event_type=event_type,
            correlation_id=self.correlation_id,
        )


async def run_optimization_cycle(project_id: str, url: str, task_id: Optional[str] = None,
                                 proposal_request: Optional[dict] = None,
                                 proposal_operation: Optional[str] = None) -> dict:
    saga = OptimizationSaga(project_id, url, task_id, proposal_request, proposal_operation)
    prepared = await saga.execute()
    return {
        "status": saga.state.value,
        "success": prepared,
        "publication_status": "not_requested",
        "reason": saga.context.get("error") or saga.context.get("needs_input"),
        "saga_id": saga.saga_id,
    }



