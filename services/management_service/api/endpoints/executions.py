"""Gateway-only authority calls; every user command also revalidates the original session."""
import hmac
from typing import Any, Callable, ParamSpec, TypeVar
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from services.management_service.approval_identity import authenticated_actor, require_service_identity
from services.management_service.approval_policy import ApprovalError, UserActor
from services.management_service.config import settings
from services.management_service.db.session import get_db
from services.management_service.execution_service import ExecutionService
from shared.hitl_execution import ExecutionError, ExecutionRequest, PreparationFailure, PreparationLease, RecoveryClaim, RecoveryReport, UUID_PATTERN, WordPressBinding
from services.management_service.execution_recovery import ExecutionRecovery
from services.management_service.execution_preparation import ExecutionPreparation
from services.management_service.execution_verification import ExecutionVerification
from shared.hitl_rollback import RollbackRequest
from shared.hitl_verification import PublicObservation, VerificationReport


def executor_identity(x_executor_key: str | None = Header(default=None)) -> None:
    expected = settings.EXECUTOR_API_KEY
    if not expected or len(expected) < 32:
        raise HTTPException(503, "executor_identity_unconfigured")
    if x_executor_key is None or not hmac.compare_digest(x_executor_key.encode(), expected.encode()):
        raise HTTPException(401, "executor_identity_required")


router = APIRouter(dependencies=[Depends(require_service_identity), Depends(executor_identity)])


def _service(db: Session) -> ExecutionService:
    return ExecutionService(db, fresh_days=settings.DOMAIN_VERIFICATION_FRESH_DAYS,
                            verification_deadline_seconds=settings.VERIFICATION_DEADLINE_SECONDS)


class ReserveRequest(ExecutionRequest):
    binding: WordPressBinding
    before: dict[str, Any]
    lease_id: str | None = Field(default=None, pattern=UUID_PATTERN)
    public_baseline: PublicObservation | None = None


class FinishRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    receipt: dict[str, Any] | None


P = ParamSpec("P")
T = TypeVar("T")


def _call(function: Callable[P, T], *args: P.args, **kwargs: P.kwargs) -> T:
    try:
        return function(*args, **kwargs)
    except ApprovalError as exc:
        raise HTTPException(exc.http_status, exc.code) from exc
    except ExecutionError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/context")
def context(body: ExecutionRequest, actor: UserActor = Depends(authenticated_actor), db: Session = Depends(get_db)) -> dict[str, Any]:
    return _call(_service(db).context, body, actor)


@router.post("/reserve")
def reserve(body: ReserveRequest, actor: UserActor = Depends(authenticated_actor), db: Session = Depends(get_db)) -> dict[str, Any]:
    references = ExecutionRequest.model_validate(body.model_dump(exclude={"binding", "before", "lease_id", "public_baseline"}))
    return _call(_service(db).reserve, references, actor, body.binding, body.before, preparation_lease=body.lease_id, public_baseline=body.public_baseline)


@router.post("/queue")
def queue(body: ExecutionRequest, actor: UserActor = Depends(authenticated_actor), db: Session = Depends(get_db)) -> dict[str, Any]:
    return _call(_service(db).queue, body, actor)


def _preparation(db: Session) -> ExecutionPreparation:
    return ExecutionPreparation(db, fresh_days=settings.DOMAIN_VERIFICATION_FRESH_DAYS)


@router.post("/{operation_id}/preparation/claim")
def preparation_claim(operation_id: UUID, actor: UserActor = Depends(authenticated_actor), db: Session = Depends(get_db)) -> dict[str, Any]:
    return _call(_preparation(db).claim, str(operation_id), actor)


@router.post("/{operation_id}/preparation/ready")
def preparation_ready(operation_id: UUID, body: PreparationLease, db: Session = Depends(get_db)) -> dict[str, Any]:
    return _call(_preparation(db).ready, str(operation_id), body.lease_id)


@router.post("/{operation_id}/preparation/fail")
def preparation_fail(operation_id: UUID, body: PreparationFailure, db: Session = Depends(get_db)) -> dict[str, Any]:
    return _call(_preparation(db).fail, str(operation_id), body)


@router.post("/{operation_id}/cancel")
def cancel(operation_id: UUID, actor: UserActor = Depends(authenticated_actor), db: Session = Depends(get_db)) -> dict[str, Any]:
    return _call(_preparation(db).cancel, str(operation_id), actor)


@router.post("/recovery/claim")
def recovery_claim(body: RecoveryClaim, db: Session = Depends(get_db)) -> dict[str, Any]:
    # Executor identity may only inspect an already-began operation. No actorless begin/apply.
    return _call(ExecutionRecovery(db).claim, body.operation_id)


@router.post("/{operation_id}/rollback/queue")
def rollback_queue(operation_id: UUID, body: RollbackRequest, actor: UserActor = Depends(authenticated_actor), db: Session = Depends(get_db)) -> dict[str, Any]:
    return _call(_service(db).queue_rollback, str(operation_id), body, actor)


@router.post("/verification/claim")
def verification_claim(body: RecoveryClaim, db: Session = Depends(get_db)) -> dict[str, Any]:
    return _call(ExecutionVerification(db, deadline_seconds=settings.VERIFICATION_DEADLINE_SECONDS).claim, body.operation_id)


@router.post("/verification/{operation_id}/report")
def verification_report(operation_id: UUID, body: VerificationReport, db: Session = Depends(get_db)) -> dict[str, Any]:
    return _call(ExecutionVerification(db, deadline_seconds=settings.VERIFICATION_DEADLINE_SECONDS).report, str(operation_id), body)


@router.post("/recovery/{operation_id}/report")
def recovery_report(operation_id: UUID, body: RecoveryReport, db: Session = Depends(get_db)) -> dict[str, Any]:
    return _call(ExecutionRecovery(db, verification_deadline_seconds=settings.VERIFICATION_DEADLINE_SECONDS).report, str(operation_id), body)


@router.get("/{operation_id}")
def read(operation_id: UUID, actor: UserActor = Depends(authenticated_actor), db: Session = Depends(get_db)) -> dict[str, Any]:
    return _call(_service(db).read, str(operation_id), actor)


@router.post("/{operation_id}/begin")
def begin(operation_id: UUID, body: PreparationLease, actor: UserActor = Depends(authenticated_actor), db: Session = Depends(get_db)) -> dict[str, Any]:
    return _call(_service(db).begin, str(operation_id), actor, preparation_lease=body.lease_id)


@router.post("/{operation_id}/finish")
def finish(operation_id: UUID, body: FinishRequest, db: Session = Depends(get_db)) -> dict[str, Any]:
    return _call(_service(db).finish, str(operation_id), receipt=body.receipt)
