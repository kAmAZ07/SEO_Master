# services/management_service/hitl_handler.py (ДОПОЛНЕННАЯ ВЕРСИЯ)

from typing import Dict, Any, Optional, List
from services.management_service.approval_policy import ApprovalError
from sqlalchemy.orm import Session

from services.management_service.db.models import (
    Task,
    HITLApproval,
    HITLStatus
)
from services.management_service.schemas.hitl import (
    HITLApprovalCreate,
    HITLDecision,
    HITLBatchApproveResponse
)
from services.management_service.approval_ledger import read_approved_notification
from services.management_service.proposal_handoff import ProposalHandoffError
from config.logging_config import get_logger
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

        raise ApprovalError("version_bound_preview_required")

    async def approve_task(
        self,
        task_id: str,
        approved_by: str,
        decision: HITLDecision,
        correlation_id: Optional[str] = None
    ) -> Dict[str, Any]:
        if decision.auto_deploy:
            raise ProposalHandoffError("version_bound_executor_required")
        raise ApprovalError("verified_user_decision_required")

    def reject_task(
        self,
        task_id: str,
        rejected_by: str,
        decision: HITLDecision,
        correlation_id: Optional[str] = None
    ) -> Dict[str, Any]:

        raise ApprovalError("verified_user_decision_required")

    async def batch_approve_tasks(
        self,
        task_ids: List[str],
        approved_by: str,
        auto_deploy: bool = False,
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

    return read_approved_notification(db, event)


def handle_hitl_rejected_event(
    db: Session,
    event: Dict[str, Any],
    correlation_id: Optional[str] = None
) -> Dict[str, Any]:

    raise ApprovalError("decision_notification_cannot_make_human_decision")


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

