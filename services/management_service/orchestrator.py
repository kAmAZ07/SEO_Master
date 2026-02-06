import asyncio
from sqlalchemy.orm import Session
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
import httpx
from enum import Enum
import uuid

from app.core.config import settings
from app.core.logging import logger
from app.db.models import Project, Task, TaskStatus, HITLDecision, HITLStatus, SagaExecution
from app.events.publishers import publish_event
from tenacity import retry, stop_after_attempt, wait_exponential


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
        
    async def execute(self) -> bool:
        from app.db.session import SessionLocal
        
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
                
                hitl_decision = await self._create_hitl_decision(db)
                approved = await self._wait_for_hitl_decision(db, hitl_decision.id)
                
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
                
        except Exception as e:
            logger.error(
                f"Saga failed for project {self.project_id}, url {self.url}: {e}",
                extra={"correlation_id": self.correlation_id, "saga_id": self.saga_id},
                exc_info=True
            )
            self.state = SagaState.FAILED
            await self._compensate()
            await self._update_task_status(db, TaskStatus.FAILED)
            await self._save_saga_state(db)
            await self._publish_completion_event(success=False, reason=str(e))
            return False
        finally:
            db.close()
    
    @retry(stop=stop_after_attempt(settings.SAGA_RETRY_MAX_ATTEMPTS), wait=wait_exponential(multiplier=1, min=4, max=10))
    async def _run_crawl(self):
        self.state = SagaState.CRAWLING
        
        response = await self.client.post(
            f"{settings.AUDIT_SERVICE_URL}/internal/crawl",
            json={
                "project_id": self.project_id,
                "urls": [self.url],
                "options": {
                    "js_render": True,
                    "priority": "high"
                }
            },
            headers={
                "X-Internal-API-Key": settings.INTERNAL_API_KEY,
                "X-Correlation-ID": self.correlation_id
            }
        )
        response.raise_for_status()
        
        result = response.json()
        self.context["crawl_id"] = result["crawl_id"]
        logger.info(
            f"Crawl initiated: {self.context['crawl_id']}",
            extra={"correlation_id": self.correlation_id, "saga_id": self.saga_id}
        )
    
    async def _wait_for_crawl_completion(self):
        timeout = datetime.utcnow() + timedelta(minutes=settings.SAGA_TIMEOUT_MINUTES)
        
        while datetime.utcnow() < timeout:
            response = await self.client.get(
                f"{settings.AUDIT_SERVICE_URL}/internal/crawl/{self.context['crawl_id']}",
                headers={
                    "X-Internal-API-Key": settings.INTERNAL_API_KEY,
                    "X-Correlation-ID": self.correlation_id
                }
            )
            response.raise_for_status()
            
            status = response.json()["status"]
            
            if status == "completed":
                self.state = SagaState.CRAWL_COMPLETED
                self.context["crawl_result"] = response.json()
                return
            elif status == "failed":
                raise Exception(f"Crawl failed: {self.context['crawl_id']}")
            
            await asyncio.sleep(5)
        
        raise TimeoutError("Crawl timeout")
    
    @retry(stop=stop_after_attempt(settings.SAGA_RETRY_MAX_ATTEMPTS), wait=wait_exponential(multiplier=1, min=4, max=10))
    async def _trigger_scores_calculation(self):
        self.state = SagaState.CALCULATING_SCORES
        
        ffscore_response = await self.client.post(
            f"{settings.SEMANTIC_SERVICE_URL}/internal/ff-score/calculate",
            json={
                "project_id": self.project_id,
                "url": self.url,
                "crawl_id": self.context["crawl_id"]
            },
            headers={
                "X-Internal-API-Key": settings.INTERNAL_API_KEY,
                "X-Correlation-ID": self.correlation_id
            }
        )
        ffscore_response.raise_for_status()
        self.context["ffscore_task_id"] = ffscore_response.json()["task_id"]
        
        eeat_response = await self.client.post(
            f"{settings.SEMANTIC_SERVICE_URL}/internal/eeat-score/calculate",
            json={
                "project_id": self.project_id,
                "url": self.url,
                "crawl_id": self.context["crawl_id"]
            },
            headers={
                "X-Internal-API-Key": settings.INTERNAL_API_KEY,
                "X-Correlation-ID": self.correlation_id
            }
        )
        eeat_response.raise_for_status()
        self.context["eeat_task_id"] = eeat_response.json()["task_id"]
    
    async def _wait_for_scores_completion(self):
        timeout = datetime.utcnow() + timedelta(minutes=settings.SAGA_TIMEOUT_MINUTES)
        
        ffscore_completed = False
        eeat_completed = False
        
        while datetime.utcnow() < timeout:
            if not ffscore_completed:
                ff_response = await self.client.get(
                    f"{settings.SEMANTIC_SERVICE_URL}/internal/ff-score/task/{self.context['ffscore_task_id']}",
                    headers={
                        "X-Internal-API-Key": settings.INTERNAL_API_KEY,
                        "X-Correlation-ID": self.correlation_id
                    }
                )
                ff_response.raise_for_status()
                ff_data = ff_response.json()
                
                if ff_data["status"] == "completed":
                    self.context["ffscore"] = ff_data["score"]
                    ffscore_completed = True
                elif ff_data["status"] == "failed":
                    raise Exception("FF-Score calculation failed")
            
            if not eeat_completed:
                eeat_response = await self.client.get(
                    f"{settings.SEMANTIC_SERVICE_URL}/internal/eeat-score/task/{self.context['eeat_task_id']}",
                    headers={
                        "X-Internal-API-Key": settings.INTERNAL_API_KEY,
                        "X-Correlation-ID": self.correlation_id
                    }
                )
                eeat_response.raise_for_status()
                eeat_data = eeat_response.json()
                
                if eeat_data["status"] == "completed":
                    self.context["eeat_score"] = eeat_data["score"]
                    eeat_completed = True
                elif eeat_data["status"] == "failed":
                    raise Exception("E-E-A-T calculation failed")
            
            if ffscore_completed and eeat_completed:
                self.state = SagaState.SCORES_COMPLETED
                return
            
            await asyncio.sleep(3)
        
        raise TimeoutError("Scores calculation timeout")
    
    @retry(stop=stop_after_attempt(settings.SAGA_RETRY_MAX_ATTEMPTS), wait=wait_exponential(multiplier=1, min=4, max=10))
    async def _trigger_content_generation(self):
        self.state = SagaState.GENERATING_CONTENT
        
        response = await self.client.post(
            f"{settings.SEMANTIC_SERVICE_URL}/internal/content/generate",
            json={
                "project_id": self.project_id,
                "url": self.url,
                "crawl_id": self.context["crawl_id"],
                "ffscore": self.context["ffscore"],
                "eeat_score": self.context["eeat_score"]
            },
            headers={
                "X-Internal-API-Key": settings.INTERNAL_API_KEY,
                "X-Correlation-ID": self.correlation_id
            }
        )
        response.raise_for_status()
        
        result = response.json()
        self.context["content_generation_id"] = result["generation_id"]
    
    async def _wait_for_content_generation(self):
        timeout = datetime.utcnow() + timedelta(minutes=settings.SAGA_TIMEOUT_MINUTES)
        
        while datetime.utcnow() < timeout:
            response = await self.client.get(
                f"{settings.SEMANTIC_SERVICE_URL}/internal/content/generation/{self.context['content_generation_id']}",
                headers={
                    "X-Internal-API-Key": settings.INTERNAL_API_KEY,
                    "X-Correlation-ID": self.correlation_id
                }
            )
            response.raise_for_status()
            
            status_data = response.json()
            
            if status_data["status"] == "completed":
                self.state = SagaState.CONTENT_GENERATED
                self.context["generated_content"] = status_data["content"]
                return
            elif status_data["status"] == "failed":
                raise Exception("Content generation failed")
            
            await asyncio.sleep(3)
        
        raise TimeoutError("Content generation timeout")
    
    async def _create_hitl_decision(self, db: Session) -> HITLDecision:
        self.state = SagaState.AWAITING_HITL
        
        crawl_data = self.context["crawl_result"]
        old_content = {
            "title": crawl_data.get("title"),
            "description": crawl_data.get("description"),
            "h1": crawl_data.get("h1"),
            "schema_org": crawl_data.get("schema_org")
        }
        
        new_content = self.context["generated_content"]
        
        decision = HITLDecision(
            project_id=self.project_id,
            task_id=self.task_id,
            url=self.url,
            change_type="content_update",
            old_content=old_content,
            new_content=new_content,
            status=HITLStatus.PENDING,
            expires_at=datetime.utcnow() + timedelta(hours=settings.HITL_TIMEOUT_HOURS),
            metadata={
                "saga_id": self.saga_id,
                "correlation_id": self.correlation_id,
                "ffscore": self.context.get("ffscore"),
                "eeat_score": self.context.get("eeat_score")
            }
        )
        
        db.add(decision)
        db.commit()
        db.refresh(decision)
        
        await publish_event(
            exchange="seo.management",
            routing_key="management.hitl.approval_required",
            event_type="HITLApprovalRequired",
            payload={
                "decision_id": str(decision.id),
                "project_id": self.project_id,
                "url": self.url,
                "expires_at": decision.expires_at.isoformat(),
                "correlation_id": self.correlation_id
            }
        )
        
        return decision
    
    async def _wait_for_hitl_decision(self, db: Session, decision_id: str) -> bool:
        timeout = datetime.utcnow() + timedelta(hours=settings.HITL_TIMEOUT_HOURS)
        
        while datetime.utcnow() < timeout:
            decision = db.query(HITLDecision).filter(HITLDecision.id == decision_id).first()
            
            if decision.status == HITLStatus.APPROVED:
                self.state = SagaState.HITL_APPROVED
                return True
            elif decision.status == HITLStatus.REJECTED:
                self.state = SagaState.HITL_REJECTED
                return False
            
            await asyncio.sleep(5)
            db.refresh(decision)
        
        raise TimeoutError("HITL decision timeout")
    
    @retry(stop=stop_after_attempt(settings.SAGA_RETRY_MAX_ATTEMPTS), wait=wait_exponential(multiplier=1, min=4, max=10))
    async def _apply_changes(self):
        self.state = SagaState.APPLYING_CHANGES
        
        response = await self.client.post(
            f"{settings.CLIENT_GATEWAY_URL}/internal/changes/queue",
            json={
                "project_id": self.project_id,
                "url": self.url,
                "changes": self.context["generated_content"]
            },
            headers={
                "X-Internal-API-Key": settings.INTERNAL_API_KEY,
                "X-Correlation-ID": self.correlation_id
            }
        )
        response.raise_for_status()
        
        result = response.json()
        self.context["change_id"] = result["change_id"]
    
    async def _wait_for_changes_applied(self):
        timeout = datetime.utcnow() + timedelta(minutes=settings.SAGA_TIMEOUT_MINUTES)
        
        while datetime.utcnow() < timeout:
            response = await self.client.get(
                f"{settings.CLIENT_GATEWAY_URL}/internal/changes/{self.context['change_id']}/status",
                headers={
                    "X-Internal-API-Key": settings.INTERNAL_API_KEY,
                    "X-Correlation-ID": self.correlation_id
                }
            )
            response.raise_for_status()
            
            status_data = response.json()
            
            if status_data["status"] == "applied":
                return
            elif status_data["status"] == "failed":
                raise Exception("Changes application failed")
            
            await asyncio.sleep(5)
        
        raise TimeoutError("Changes application timeout")
    
    async def _compensate(self):
        self.state = SagaState.COMPENSATING
        logger.info(
            f"Starting compensation for saga",
            extra={
                "correlation_id": self.correlation_id,
                "saga_id": self.saga_id,
                "project_id": self.project_id,
                "url": self.url
            }
        )
        
        if "change_id" in self.context:
            try:
                async with httpx.AsyncClient(timeout=settings.SERVICE_REQUEST_TIMEOUT) as client:
                    await client.post(
                        f"{settings.CLIENT_GATEWAY_URL}/internal/changes/{self.context['change_id']}/rollback",
                        headers={
                            "X-Internal-API-Key": settings.INTERNAL_API_KEY,
                            "X-Correlation-ID": self.correlation_id
                        }
                    )
            except Exception as e:
                logger.error(f"Compensation rollback failed: {e}", extra={"correlation_id": self.correlation_id})
    
    async def _update_task_status(self, db: Session, status: TaskStatus):
        if self.task_id:
            task = db.query(Task).filter(Task.id == self.task_id).first()
            if task:
                task.status = status
                task.updated_at = datetime.utcnow()
                db.commit()
    
    async def _save_saga_state(self, db: Session):
        saga_execution = db.query(SagaExecution).filter(SagaExecution.saga_id == self.saga_id).first()
        
        if not saga_execution:
            saga_execution = SagaExecution(
                saga_id=self.saga_id,
                project_id=self.project_id,
                url=self.url,
                task_id=self.task_id,
                state=self.state,
                context=self.context,
                correlation_id=self.correlation_id
            )
            db.add(saga_execution)
        else:
            saga_execution.state = self.state
            saga_execution.context = self.context
            saga_execution.updated_at = datetime.utcnow()
        
        db.commit()
    
    async def _publish_completion_event(self, success: bool, reason: Optional[str] = None):
        event_type = "OptimizationCompleted" if success else "OptimizationFailed"
        
        await publish_event(
            exchange="seo.management",
            routing_key=f"management.optimization.{'completed' if success else 'failed'}",
            event_type=event_type,
            payload={
                "saga_id": self.saga_id,
                "project_id": self.project_id,
                "url": self.url,
                "task_id": self.task_id,
                "success": success,
                "reason": reason,
                "ffscore": self.context.get("ffscore"),
                "eeat_score": self.context.get("eeat_score"),
                "correlation_id": self.correlation_id
            }
        )


async def run_optimization_cycle(project_id: str, url: str, task_id: Optional[str] = None) -> bool:
    saga = OptimizationSaga(project_id, url, task_id)
    return await saga.execute()
