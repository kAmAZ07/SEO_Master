# services/management_service/hitl_handler.py (ДОПОЛНЕННАЯ ВЕРСИЯ)

from typing import Dict, Any, Optional, List
from datetime import datetime
from sqlalchemy.orm import Session

from services.management_service.db.models import (
    Task,
    TaskStatus,
    HITLApproval,
    HITLStatus,
    Project
)
from services.management_service.schemas.hitl import (
    HITLApprovalCreate,
    HITLDecision,
    HITLBatchApproveRequest,
    HITLBatchApproveResponse
)
from services.management_service.client_api_adapter import deploy_task_changes
from services.management_service.events.hitl_approved import publish_hitl_approved_event
from config.loggingconfig import get_logger
from prometheus_client import Counter, Histogram

logger = get_logger(__name__)

hitl_approvals_total = Counter(
    'hitl_approvals_total',
    'Total HITL approvals processed',
    ['status']
)

hitl_processing_duration = Histogram(
    'hitl_processing_duration_seconds',
    'Duration of HITL approval processing'
)

hitl_errors_total = Counter(
    'hitl_errors_total',
    'Total HITL processing errors',
    ['error_type']
)


class HITLHandler:
    
    def __init__(self, db: Session):
        self.db = db
    
    def create_hitl_approval(
        self,
        task_id: str,
        approval_data: HITLApprovalCreate,
        correlation_id: Optional[str] = None
    ) -> HITLApproval:
        
        task = self.db.query(Task).filter(Task.id == task_id).first()
        
        if not task:
            raise ValueError(f"Task {task_id} not found")
        
        existing_approval = self.db.query(HITLApproval).filter(
            HITLApproval.task_id == task_id
        ).first()
        
        if existing_approval:
            raise ValueError(f"HITL approval already exists for task {task_id}")
        
        hitl_approval = HITLApproval(
            task_id=task_id,
            project_id=task.project_id,
            status=HITLStatus.PENDING,
            diff_data=approval_data.diff_data,
            impact_score=approval_data.impact_score,
            recommendation=approval_data.recommendation,
            metadata={
                "correlation_id": correlation_id,
                "created_by": "management_service"
            }
        )
        
        task.status = TaskStatus.PENDING
        
        self.db.add(hitl_approval)
        self.db.add(task)
        self.db.commit()
        self.db.refresh(hitl_approval)
        
        logger.info(
            f"Created HITL approval for task {task_id}",
            extra={
                "task_id": task_id,
                "project_id": task.project_id,
                "impact_score": approval_data.impact_score,
                "correlation_id": correlation_id
            }
        )
        
        return hitl_approval
    
    async def approve_task(
        self,
        task_id: str,
        approved_by: str,
        decision: HITLDecision,
        correlation_id: Optional[str] = None
    ) -> Dict[str, Any]:
        
        with hitl_processing_duration.time():
            task = self.db.query(Task).filter(Task.id == task_id).first()
            
            if not task:
                hitl_errors_total.labels(error_type='task_not_found').inc()
                raise ValueError(f"Task {task_id} not found")
            
            hitl_approval = self.db.query(HITLApproval).filter(
                HITLApproval.task_id == task_id
            ).first()
            
            if not hitl_approval:
                hitl_errors_total.labels(error_type='approval_not_found').inc()
                raise ValueError(f"HITL approval not found for task {task_id}")
            
            if hitl_approval.status != HITLStatus.PENDING:
                hitl_errors_total.labels(error_type='already_processed').inc()
                raise ValueError(f"HITL approval already processed with status {hitl_approval.status.value}")
            
            hitl_approval.status = HITLStatus.APPROVED
            hitl_approval.approved_by = approved_by
            hitl_approval.approved_at = datetime.utcnow()
            
            if decision.notes:
                hitl_approval.metadata = {
                    **(hitl_approval.metadata or {}),
                    "approval_notes": decision.notes
                }
            
            task.status = TaskStatus.APPROVED
            task.metadata = {
                **(task.metadata or {}),
                "approved_by": approved_by,
                "approved_at": datetime.utcnow().isoformat()
            }
            
            self.db.add(hitl_approval)
            self.db.add(task)
            self.db.commit()
            
            hitl_approvals_total.labels(status='approved').inc()
            
            logger.info(
                f"Task {task_id} approved by {approved_by}",
                extra={
                    "task_id": task_id,
                    "project_id": task.project_id,
                    "approved_by": approved_by,
                    "correlation_id": correlation_id
                }
            )
            
            deployment_result = None
            
            if decision.auto_deploy:
                try:
                    deployment_result = await deploy_task_changes(
                        self.db,
                        task,
                        correlation_id
                    )
                    
                    logger.info(
                        f"Task {task_id} automatically deployed after approval",
                        extra={
                            "task_id": task_id,
                            "correlation_id": correlation_id
                        }
                    )
                    
                except Exception as e:
                    logger.error(
                        f"Failed to auto-deploy task {task_id}: {e}",
                        extra={
                            "task_id": task_id,
                            "correlation_id": correlation_id
                        }
                    )
                    hitl_errors_total.labels(error_type='deployment_failed').inc()
            
            try:
                await publish_hitl_approved_event(
                    db=self.db,
                    task_id=str(task.id),
                    project_id=str(task.project_id),
                    approved_by=approved_by,
                    auto_deployed=decision.auto_deploy,
                    correlation_id=correlation_id
                )
            except Exception as e:
                logger.error(
                    f"Failed to publish HITLApproved event: {e}",
                    extra={"task_id": task_id, "correlation_id": correlation_id}
                )
            
            return {
                "task_id": str(task.id),
                "status": "approved",
                "approved_by": approved_by,
                "approved_at": hitl_approval.approved_at.isoformat(),
                "auto_deployed": decision.auto_deploy,
                "deployment_result": deployment_result
            }
    
    def reject_task(
        self,
        task_id: str,
        rejected_by: str,
        decision: HITLDecision,
        correlation_id: Optional[str] = None
    ) -> Dict[str, Any]:
        
        task = self.db.query(Task).filter(Task.id == task_id).first()
        
        if not task:
            hitl_errors_total.labels(error_type='task_not_found').inc()
            raise ValueError(f"Task {task_id} not found")
        
        hitl_approval = self.db.query(HITLApproval).filter(
            HITLApproval.task_id == task_id
        ).first()
        
        if not hitl_approval:
            hitl_errors_total.labels(error_type='approval_not_found').inc()
            raise ValueError(f"HITL approval not found for task {task_id}")
        
        if hitl_approval.status != HITLStatus.PENDING:
            hitl_errors_total.labels(error_type='already_processed').inc()
            raise ValueError(f"HITL approval already processed with status {hitl_approval.status.value}")
        
        hitl_approval.status = HITLStatus.REJECTED
        hitl_approval.rejected_by = rejected_by
        hitl_approval.rejected_at = datetime.utcnow()
        hitl_approval.rejection_reason = decision.rejection_reason or "No reason provided"
        
        if decision.notes:
            hitl_approval.metadata = {
                **(hitl_approval.metadata or {}),
                "rejection_notes": decision.notes
            }
        
        task.status = TaskStatus.REJECTED
        task.metadata = {
            **(task.metadata or {}),
            "rejected_by": rejected_by,
            "rejected_at": datetime.utcnow().isoformat(),
            "rejection_reason": decision.rejection_reason
        }
        
        self.db.add(hitl_approval)
        self.db.add(task)
        self.db.commit()
        
        hitl_approvals_total.labels(status='rejected').inc()
        
        logger.info(
            f"Task {task_id} rejected by {rejected_by}",
            extra={
                "task_id": task_id,
                "project_id": task.project_id,
                "rejected_by": rejected_by,
                "reason": decision.rejection_reason,
                "correlation_id": correlation_id
            }
        )
        
        return {
            "task_id": str(task.id),
            "status": "rejected",
            "rejected_by": rejected_by,
            "rejected_at": hitl_approval.rejected_at.isoformat(),
            "rejection_reason": decision.rejection_reason
        }
    
    async def batch_approve_tasks(
        self,
        task_ids: List[str],
        approved_by: str,
        auto_deploy: bool = True,
        correlation_id: Optional[str] = None
    ) -> HITLBatchApproveResponse:
        
        results = []
        approved_count = 0
        failed_count = 0
        
        for task_id in task_ids:
            try:
                decision = HITLDecision(auto_deploy=auto_deploy)
                
                result = await self.approve_task(
                    task_id=task_id,
                    approved_by=approved_by,
                    decision=decision,
                    correlation_id=correlation_id
                )
                
                results.append({
                    "task_id": task_id,
                    "success": True,
                    "result": result
                })
                approved_count += 1
                
            except Exception as e:
                results.append({
                    "task_id": task_id,
                    "success": False,
                    "error": str(e)
                })
                failed_count += 1
                
                logger.error(
                    f"Failed to approve task {task_id} in batch: {e}",
                    extra={
                        "task_id": task_id,
                        "correlation_id": correlation_id
                    }
                )
        
        logger.info(
            f"Batch approval completed: {approved_count} approved, {failed_count} failed",
            extra={
                "total": len(task_ids),
                "approved": approved_count,
                "failed": failed_count,
                "correlation_id": correlation_id
            }
        )
        
        return HITLBatchApproveResponse(
            total=len(task_ids),
            approved=approved_count,
            failed=failed_count,
            results=results
        )
    
    def get_pending_approvals(
        self,
        project_id: Optional[str] = None,
        limit: int = 50,
        offset: int = 0
    ) -> List[HITLApproval]:
        
        query = self.db.query(HITLApproval).filter(
            HITLApproval.status == HITLStatus.PENDING
        )
        
        if project_id:
            query = query.filter(HITLApproval.project_id == project_id)
        
        approvals = query.order_by(
            HITLApproval.impact_score.desc().nullslast(),
            HITLApproval.created_at.asc()
        ).offset(offset).limit(limit).all()
        
        return approvals
    
    def get_approval_by_task_id(self, task_id: str) -> Optional[HITLApproval]:
        
        return self.db.query(HITLApproval).filter(
            HITLApproval.task_id == task_id
        ).first()
    
    def get_approval_with_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        
        hitl_approval = self.db.query(HITLApproval).filter(
            HITLApproval.task_id == task_id
        ).first()
        
        if not hitl_approval:
            return None
        
        task = self.db.query(Task).filter(Task.id == task_id).first()
        
        return {
            "approval": hitl_approval,
            "task": task
        }
    
    def get_approval_statistics(self, project_id: str) -> Dict[str, Any]:
        
        total = self.db.query(HITLApproval).filter(
            HITLApproval.project_id == project_id
        ).count()
        
        pending = self.db.query(HITLApproval).filter(
            HITLApproval.project_id == project_id,
            HITLApproval.status == HITLStatus.PENDING
        ).count()
        
        approved = self.db.query(HITLApproval).filter(
            HITLApproval.project_id == project_id,
            HITLApproval.status == HITLStatus.APPROVED
        ).count()
        
        rejected = self.db.query(HITLApproval).filter(
            HITLApproval.project_id == project_id,
            HITLApproval.status == HITLStatus.REJECTED
        ).count()
        
        return {
            "project_id": project_id,
            "total": total,
            "pending": pending,
            "approved": approved,
            "rejected": rejected,
            "approval_rate": round(approved / total * 100, 2) if total > 0 else 0
        }
    
    def get_pending_count(self, project_id: Optional[str] = None) -> int:
        
        query = self.db.query(HITLApproval).filter(
            HITLApproval.status == HITLStatus.PENDING
        )
        
        if project_id:
            query = query.filter(HITLApproval.project_id == project_id)
        
        return query.count()
    
    def get_high_impact_pending(
        self,
        project_id: Optional[str] = None,
        min_impact_score: float = 0.7,
        limit: int = 20
    ) -> List[HITLApproval]:
        
        query = self.db.query(HITLApproval).filter(
            HITLApproval.status == HITLStatus.PENDING,
            HITLApproval.impact_score >= min_impact_score
        )
        
        if project_id:
            query = query.filter(HITLApproval.project_id == project_id)
        
        approvals = query.order_by(
            HITLApproval.impact_score.desc()
        ).limit(limit).all()
        
        return approvals


async def handle_hitl_approved_event(
    db: Session,
    event: Dict[str, Any],
    correlation_id: Optional[str] = None
) -> Dict[str, Any]:
    
    payload = event.get("payload", {})
    task_id = payload.get("task_id")
    approved_by = payload.get("approved_by")
    auto_deploy = payload.get("auto_deploy", True)
    notes = payload.get("notes")
    
    if not task_id:
        raise ValueError("task_id is required in event payload")
    
    if not approved_by:
        raise ValueError("approved_by is required in event payload")
    
    handler = HITLHandler(db)
    
    decision = HITLDecision(
        auto_deploy=auto_deploy,
        notes=notes
    )
    
    result = await handler.approve_task(
        task_id=task_id,
        approved_by=approved_by,
        decision=decision,
        correlation_id=correlation_id
    )
    
    logger.info(
        f"HITLApproved event processed for task {task_id}",
        extra={
            "task_id": task_id,
            "approved_by": approved_by,
            "correlation_id": correlation_id
        }
    )
    
    return result


def handle_hitl_rejected_event(
    db: Session,
    event: Dict[str, Any],
    correlation_id: Optional[str] = None
) -> Dict[str, Any]:
    
    payload = event.get("payload", {})
    task_id = payload.get("task_id")
    rejected_by = payload.get("rejected_by")
    rejection_reason = payload.get("rejection_reason")
    notes = payload.get("notes")
    
    if not task_id:
        raise ValueError("task_id is required in event payload")
    
    if not rejected_by:
        raise ValueError("rejected_by is required in event payload")
    
    handler = HITLHandler(db)
    
    decision = HITLDecision(
        rejection_reason=rejection_reason,
        notes=notes
    )
    
    result = handler.reject_task(
        task_id=task_id,
        rejected_by=rejected_by,
        decision=decision,
        correlation_id=correlation_id
    )
    
    logger.info(
        f"HITLRejected event processed for task {task_id}",
        extra={
            "task_id": task_id,
            "rejected_by": rejected_by,
            "correlation_id": correlation_id
        }
    )
    
    return result


def create_hitl_approval_for_task(
    db: Session,
    task: Task,
    diff_data: Dict[str, Any],
    impact_score: Optional[float] = None,
    recommendation: Optional[str] = None,
    correlation_id: Optional[str] = None
) -> HITLApproval:
    
    handler = HITLHandler(db)
    
    approval_data = HITLApprovalCreate(
        task_id=task.id,
        diff_data=diff_data,
        impact_score=impact_score or task.impact_score,
        recommendation=recommendation
    )
    
    return handler.create_hitl_approval(
        task_id=str(task.id),
        approval_data=approval_data,
        correlation_id=correlation_id
    )
