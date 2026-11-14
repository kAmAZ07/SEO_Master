"""Native AR-4 integration: migrated PostgreSQL authority → Gateway → real WordPress.

Identity/session HTTP and UI are tested separately; this is not staging/browser E2E.
"""
from datetime import datetime, timedelta, timezone
import os
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from database.models import DomainAuthorization, Project, User, Workspace, WorkspaceMember
from services.audit_service.db.models import CrawlResult
from services.client_api_gateway.hitl_executor import HITLExecutor
from services.management_service.approval_policy import ApprovalError, DecisionRequest, UserActor
from services.management_service.approval_service import ApprovalService
from services.management_service.execution_service import ExecutionService
from services.management_service.execution_preparation import ExecutionPreparation
from services.management_service.execution_verification import ExecutionVerification
from services.client_api_gateway.public_verifier import PublicVerifier
from services.management_service.migrations.runner import migrate
from services.management_service.models import Task, TaskStatus, TaskType
from services.semantic_service.proposals.model import ExternalEntity
from services.semantic_service.proposals.repository import row_from_version
from shared.hitl_execution import ExecutionRequest, PreparationFailure, WordPressBinding
from shared.hitl_rollback import RollbackRequest
from shared.hitl_verification import PublicObservation, VerificationReport
from shared.page_evidence import seal_evidence
from tests.fixtures.management_proposal import PROJECT_ID, TASK_ID, management_proposal_fixture
from tests.wordpress.native import NativeWordPress


async def run_authority(wp: NativeWordPress, target_factory, public_reader) -> None:
    # The admin URL is created here; never use an inherited shared TEST_PG_ADMIN_URL.
    os.environ["TEST_PG_ADMIN_URL"] = wp.start_postgres()
    from tests.integration.test_migrations_postgres import TempDatabase
    now = datetime.now(timezone.utc)
    actor_id, workspace_id = str(uuid4()), str(uuid4())
    entity_id = int(wp.php("$id=wp_insert_post(['post_type'=>'post','post_status'=>'publish','post_name'=>'approved-catalog','post_title'=>'Мастерская обуви','post_content'=>'Мастерская обуви. Изготавливаем зимнюю обувь и кожаные ботинки ручной работы.']); update_post_meta($id,'_seo_master_meta_title','Мастерская обуви'); echo $id;"))
    url = "http://wordpress.test/approved-catalog"
    request, _, _ = management_proposal_fixture(target_url=url)
    evidence = seal_evidence({"schema_version": "page_evidence_v1", "audit_id": "native-audit", "project_id": PROJECT_ID,
        "url": url, "final_url": url, "status_code": 200, "facts": request["facts"], "jsonld_blocks": 0})
    _, _, version = management_proposal_fixture(target_url=url, entity=ExternalEntity(kind="post", id=str(entity_id)),
        evidence_override=[{"evidence_id": evidence["evidence_id"], "sha256": evidence["content_hash"]}])
    with TempDatabase() as database:
        database.alembic("upgrade", "head")
        with database.engine.connect() as conn:
            migrate(conn, expected_role=database.engine.url.username or "")
        with Session(database.engine) as session:
            session.add(User(id=actor_id, email="native@example.test", hashed_password="fixture", is_active=True))
            session.add(Workspace(id=workspace_id, name="Native AR4", owner_id=actor_id, kind="personal"))
            session.flush()
            session.add(WorkspaceMember(workspace_id=workspace_id, user_id=actor_id, role="owner"))
            session.add(Project(id=PROJECT_ID, name="Native AR4", url="http://wordpress.test/", status="active",
                owner_id=actor_id, workspace_id=workspace_id, origin="http://wordpress.test", path_prefix="/"))
            session.flush()
            session.add(DomainAuthorization(id=str(uuid4()), project_id=PROJECT_ID, workspace_id=workspace_id, status="verified",
                scope_origin="http://wordpress.test", scope_path_prefix="/", challenge_hash="0" * 64, subject_user_id=actor_id,
                issued_at=now - timedelta(days=1), expires_at=now - timedelta(hours=23), verified_at=now))
            session.add(Task(id=UUID(TASK_ID), project_id=UUID(PROJECT_ID), task_type=TaskType.UPDATE_META, status=TaskStatus.PENDING, url=url))
            session.add(row_from_version(version))
            session.add(CrawlResult(audit_id="native-audit", project_id=PROJECT_ID, root_url=url, mode="full", status="completed",
                created_at=now, updated_at=now, crawled_at=now, pages=[{"url": url, "page_evidence": evidence}]))
            session.commit()
            prepared = ApprovalService(session).prepare(version, metadata={})
            approval_id = str(prepared.id)
        references = ExecutionRequest(operation_id=str(uuid4()), task_id=TASK_ID, approval_id=approval_id,
            recommendation_version_id=version.version_id, content_hash=version.content_hash)
        actor = UserActor(actor_id)
        async def authority(method, path, payload=None):
            with Session(database.engine) as session:
                service = ExecutionService(session)
                try:
                    if path == "/context":
                        return service.context(ExecutionRequest.model_validate(payload), actor)
                    if path == "/queue":
                        return service.queue(ExecutionRequest.model_validate(payload), actor)
                    if path == "/reserve":
                        return service.reserve(ExecutionRequest.model_validate({key: payload[key] for key in ExecutionRequest.model_fields}),
                            actor, WordPressBinding.model_validate(payload["binding"]), payload["before"], preparation_lease=payload["lease_id"],
                            public_baseline=PublicObservation.model_validate(payload["public_baseline"]) if payload.get("public_baseline") else None)
                    if path == "/verification/claim":
                        return ExecutionVerification(session).claim(payload["operation_id"])
                    if path.startswith("/verification/"):
                        return ExecutionVerification(session).report(path.split("/")[2], VerificationReport.model_validate(payload))
                    operation_id = path.split("/")[1]
                    if method == "GET":
                        return service.read(operation_id, actor)
                    if path.endswith("/rollback/queue"):
                        return service.queue_rollback(operation_id, RollbackRequest.model_validate(payload), actor)
                    if path.endswith("/begin"):
                        return service.begin(operation_id, actor, preparation_lease=payload["lease_id"])
                    if path.endswith("/preparation/claim"):
                        return ExecutionPreparation(session).claim(operation_id, actor)
                    if path.endswith("/preparation/ready"):
                        return ExecutionPreparation(session).ready(operation_id, payload["lease_id"])
                    if path.endswith("/preparation/fail"):
                        return ExecutionPreparation(session).fail(operation_id, PreparationFailure.model_validate(payload))
                    if path.endswith("/finish"):
                        return service.finish(operation_id, receipt=payload["receipt"])
                    raise AssertionError("unknown native authority call")
                except ApprovalError as error:
                    raise HTTPException(error.http_status, error.code) from error
        executor = HITLExecutor("Bearer fixture-session-tested-separately", authority=authority, target_factory=target_factory, public_reader=public_reader)
        def title():
            return wp.php("echo get_post_meta(" + str(entity_id) + ", '_seo_master_meta_title', true);")
        try:
            await executor.execute(references)
        except HTTPException as error:
            assert error.status_code == 409
        else:
            raise AssertionError("unapproved operation reached CMS")
        assert title() == "Мастерская обуви"
        with Session(database.engine) as session:
            decision = DecisionRequest(approval_id=approval_id, recommendation_version_id=version.version_id, content_hash=version.content_hash)
            ApprovalService(session).decide(TASK_ID, actor, decision, "approve")
        assert title() == "Мастерская обуви"  # Approval alone has zero CMS effects.
        result = await executor.execute(references)
        assert result["state"] == "applied_unverified" and title() == "Мастерская зимней обуви", (result["state"], result.get("preparation"))
        assert await executor.execute(references) == result
        with database.engine.connect() as conn:
            assert conn.scalar(text("SELECT count(*) FROM public.management_changelog WHERE source='hitl_executor_v1'")) == 1
            assert conn.scalar(text("SELECT metadata->'record'->>'actor_id' FROM public.management_changelog")) == actor_id
        verifier = PublicVerifier(authority=authority, reader=public_reader)
        verified = await verifier.once(references.operation_id)
        assert verified["state"] == "verified" and verified["verification"]["last_result"]["observation"]["status"] == 200
        rollback_request = RollbackRequest(operation_id=str(uuid4()), reason="Native acceptance: restore exact before")
        rolled_back = await executor.rollback(references.operation_id, rollback_request)
        assert rolled_back["state"] == "rollback_applied_unverified" and title() == "Мастерская обуви"
        assert rolled_back["record"]["after"] == result["record"]["before"]
        assert (await verifier.once(rollback_request.operation_id))["state"] == "rollback_verified"
        assert (await executor.rollback(references.operation_id, rollback_request))["state"] == "rollback_verified"
        with Session(database.engine) as session:
            assert session.get(Task, UUID(TASK_ID)).status == TaskStatus.CANCELLED
        print("PASS native AR7: migrated authority, exact apply and signed receipt, anonymous public verified, explicit rollback restoring exact before, separate signed rollback receipt and independent rollback_verified. Session HTTP/browser/staging tested separately.")
