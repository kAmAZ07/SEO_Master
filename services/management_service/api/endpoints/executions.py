"""Gateway-only authority calls; every user command also revalidates the original session."""
import hmac
from typing import Any, Callable, ParamSpec, TypeVar
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from services.management_service.approval_identity import authenticated_actor, require_service_identity
from services.management_service.approval_policy import ApprovalError, UserActor
from services.management_service.config import settings
from services.management_service.db.session import get_db
from services.management_service.execution_service import ExecutionService
from shared.hitl_execution import ExecutionError, ExecutionRequest, WordPressBinding


def executor_identity(x_executor_key: str | None = Header(default=None)) -> None:
    expected = settings.EXECUTOR_API_KEY
    if not expected or len(expected) < 32:
        raise HTTPException(503, "executor_identity_unconfigured")
    if x_executor_key is None or not hmac.compare_digest(x_executor_key.encode(), expected.encode()):
        raise HTTPException(401, "executor_identity_required")


router = APIRouter(dependencies=[Depends(require_service_identity), Depends(executor_identity)])


def _service(db: Session) -> ExecutionService:
    return ExecutionService(db, fresh_days=settings.DOMAIN_VERIFICATION_FRESH_DAYS)


class ReserveRequest(ExecutionRequest):
    binding: WordPressBinding
    before: dict[str, Any]


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
    references = ExecutionRequest.model_validate(body.model_dump(exclude={"binding", "before"}))
    return _call(_service(db).reserve, references, actor, body.binding, body.before)


@router.get("/{operation_id}")
def read(operation_id: UUID, actor: UserActor = Depends(authenticated_actor), db: Session = Depends(get_db)) -> dict[str, Any]:
    return _call(_service(db).read, str(operation_id), actor)


@router.post("/{operation_id}/begin")
def begin(operation_id: UUID, actor: UserActor = Depends(authenticated_actor), db: Session = Depends(get_db)) -> dict[str, Any]:
    return _call(_service(db).begin, str(operation_id), actor)


@router.post("/{operation_id}/finish")
def finish(operation_id: UUID, body: FinishRequest, db: Session = Depends(get_db)) -> dict[str, Any]:
    return _call(_service(db).finish, str(operation_id), receipt=body.receipt)
