"""Real migrated inbox, quota, replay and read-only worker; upstream is a protocol fixture."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier, Event
import time
from uuid import uuid4

from cryptography.fernet import Fernet
import pytest
from sqlalchemy import select, text, update
from sqlalchemy.orm import Session, sessionmaker

from services.client_api_gateway import tilda_sync as sync
from services.client_api_gateway.models import DeploymentLog
from services.management_service.models import HITLApproval, Task, TaskStatus, TaskType
from services.management_service.approval_service import ApprovalService
from services.management_service.approval_policy import UserActor
from services.client_api_gateway.tilda_manual import MANUAL, verify_manual
from services.project_integrations.credentials_vault import CredentialsVault
from services.project_integrations.models import ProjectIntegration
from shared.tilda_contract import PublishHint
from tests.integration import test_management_approvals_postgres as approvals
from tests.integration import test_management_execution_postgres as execution
from tests.integration.test_migrations_postgres import ADMIN_URL
from tests.fixtures.management_proposal import PROJECT_ID, TASK_ID, TARGET_URL

pytestmark = pytest.mark.skipif(not ADMIN_URL, reason='TEST_PG_ADMIN_URL is not set')
approval_stand = approvals.stand
NOW = datetime.now(timezone.utc)


def test_even_approved_tilda_task_cannot_queue_an_automatic_cms_operation(approval_stand):
    from services.management_service.approval_policy import ApprovalError
    from services.management_service.execution_service import ExecutionService
    from services.management_service.models import Changelog
    engine, _, _, references = execution.executable.__wrapped__(approval_stand)
    with Session(engine) as db:
        db.get(Task, sync.UUID(TASK_ID)).task_type = TaskType.UPDATE_TILDA_PAGE
        db.commit()
        with pytest.raises(ApprovalError, match='write_unsupported'):
            ExecutionService(db, clock=lambda: execution.NOW).queue(references, UserActor(approvals.ALICE))
        db.rollback()
        assert db.query(Changelog).count() == 0
        assert db.get(Task, sync.UUID(TASK_ID)).status == TaskStatus.APPROVED


@pytest.fixture()
def stand(approval_stand, monkeypatch):
    engine, _, _ = approval_stand
    key = Fernet.generate_key().decode()
    monkeypatch.setenv('MASTER_ENCRYPTION_KEY', key)
    monkeypatch.setattr(sync.settings, 'TILDA_WEBHOOK_SECRET', 's' * 32)
    monkeypatch.setattr(sync.settings, 'TILDA_WEBHOOK_PUBLIC_URL', 'https://app.example/api/tilda/webhook')
    with Session(engine) as db:
        db.add(ProjectIntegration(project_id=PROJECT_ID, platform='tilda',
            encrypted_creds=CredentialsVault(key).encrypt({'public_key': 'public-fixture', 'secret_key': 'secret-fixture', 'project_id': '42'}),
            creds_hint='fixture', details={'external_project_id': '42', 'page_mappings': {}}))
        db.commit()
    return engine


def hint(**changes):
    return PublishHint(binding=PROJECT_ID, token=sync.callback_token(PROJECT_ID, '42', 'public-fixture'),
        **{'projectid': '42', 'pageid': '123', 'published': str(int(NOW.timestamp())), 'publickey': 'public-fixture', **changes})


def enqueue(engine, value=None):
    with Session(engine) as db:
        return sync.enqueue_hint(db, value or hint(), now=NOW)


def test_hint_ack_is_durable_deduplicated_and_never_creates_a_task_or_mapping(stand):
    first = enqueue(stand)
    assert first['status'] == 'queued'
    assert enqueue(stand) == {'status': 'duplicate', 'job_id': first['job_id']}
    with Session(stand) as db:
        row = db.query(DeploymentLog).one()
        assert row.status == 'queued' and row.applied_at is None and row.task_id is None
        assert 'secret-fixture' not in str(row.meta) and 'token' not in row.meta and 'publickey' not in row.meta
        assert db.scalar(text('SELECT count(*) FROM public.tasks')) == 1
        assert db.scalar(text("SELECT details->'page_mappings' FROM audit_schema.project_integrations WHERE project_id=:id"), {'id': PROJECT_ID}) == {}


def test_wrong_binding_or_upstream_identity_cannot_queue_a_hint(stand):
    for changes in ({'publickey': 'other'}, {'projectid': '43'}, {'published': '1'}):
        with pytest.raises(sync.TildaSyncError):
            enqueue(stand, hint(**changes))
    with Session(stand) as db:
        assert db.query(DeploymentLog).count() == 0


def test_same_domain_and_external_id_do_not_merge_projects_or_callback_authorities(stand):
    from database.models import Project, Workspace, WorkspaceMember
    other_project, other_workspace = str(uuid4()), str(uuid4())
    with Session(stand) as db:
        db.add(Workspace(id=other_workspace, name='Other owner', kind='personal', owner_id=approvals.BOB))
        db.flush()
        db.add(WorkspaceMember(workspace_id=other_workspace, user_id=approvals.BOB, role='owner'))
        db.add(Project(id=other_project, owner_id=approvals.BOB, workspace_id=other_workspace, name='Same domain',
            url=TARGET_URL, origin='https://example.com', path_prefix='/', status='active'))
        db.flush()
        original = db.query(ProjectIntegration).one()
        db.add(ProjectIntegration(project_id=other_project, platform='tilda', encrypted_creds=original.encrypted_creds,
            creds_hint='fixture', details={'external_project_id': '42', 'page_mappings': {}}))
        db.commit()
        crossed = hint().model_copy(update={'binding': other_project})
        with pytest.raises(sync.TildaSyncError, match='tilda_binding_not_found'):
            sync.enqueue_hint(db, crossed)
        valid = crossed.model_copy(update={'token': sync.callback_token(other_project, '42', 'public-fixture')})
        own = sync.enqueue_hint(db, valid)
        first = enqueue(stand)
        assert own['job_id'] != first['job_id']
        rows = db.query(DeploymentLog).all()
        assert {row.project_id for row in rows} == {PROJECT_ID, other_project}


def test_concurrent_replays_commit_one_row(stand):
    barrier = Barrier(4)
    def worker(_):
        barrier.wait(timeout=10)
        return enqueue(stand)['status']
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(worker, range(4)))
    assert results.count('queued') == 1 and results.count('duplicate') == 3
    with Session(stand) as db:
        assert db.query(DeploymentLog).count() == 1


def test_quota_is_shared_by_credential_fingerprint_and_reservations_survive_restart(stand):
    fingerprint = sync._fingerprint('public-fixture')
    with Session(stand) as db:
        for _ in range(149):
            sync.reserve_read(db, PROJECT_ID, fingerprint, 'getpage', now=NOW)
    barrier = Barrier(2)
    def worker(_):
        barrier.wait(timeout=10)
        with Session(stand) as db:
            try:
                sync.reserve_read(db, PROJECT_ID, fingerprint, 'getpage', now=NOW)
                return 'reserved'
            except sync.TildaSyncError as error:
                return error.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(worker, range(2))) == ['reserved', 'tilda_quota_exceeded']
    with Session(stand) as db:
        assert db.query(DeploymentLog).count() == 150
        sync.reserve_read(db, PROJECT_ID, fingerprint, 'getpage', now=NOW + timedelta(hours=1, seconds=1))


class Reader(sync.TildaReader):
    def __init__(self, engine, mismatch=False):
        super().__init__(sessionmaker(bind=engine))
        self.calls = []
        self.mismatch = mismatch

    async def _read(self, project_id, credentials, method, page_id=None):
        self.calls.append(method)
        if method == 'getprojectinfo':
            return {'id': '42', 'customdomain': 'example.com'}
        value = {'id': '123', 'projectid': '43' if self.mismatch else '42', 'title': '', 'descr': None, 'filename': 'page'}
        return value if method == 'getpage' else [value]


def test_worker_restart_refetches_authorized_source_and_preserves_empty_null_without_publish(stand):
    enqueue(stand)
    reader = Reader(stand)
    assert asyncio.run(reader.once())
    with Session(stand) as db:
        row = db.query(DeploymentLog).one()
        assert row.status == 'manual_required' and row.applied_at is None
        fields = row.changes['pages'][0]['fields']
        assert fields['title'] == '' and fields['descr'] is None and 'h1' not in fields
        assert row.changes['receipt'] is None
    assert not asyncio.run(Reader(stand).once())
    assert reader.calls == ['getprojectinfo', 'getpage']


def test_mismatched_refetch_cannot_create_mapping_or_verified_result(stand):
    enqueue(stand)
    asyncio.run(Reader(stand, mismatch=True).once())
    with Session(stand) as db:
        row = db.query(DeploymentLog).one()
        assert row.status == 'read_failed' and row.error_message == 'tilda_scope_mismatch' and row.changes == {}


def test_expired_lease_is_recovered_after_restart_without_any_cms_command(stand):
    item = enqueue(stand)
    reader = Reader(stand)
    claimed = reader._claim()
    assert claimed and not Reader(stand)._claim()
    with Session(stand) as db:
        meta = {**claimed['metadata'], 'lease_until': (NOW - timedelta(seconds=1)).isoformat()}
        db.execute(update(sync.TABLE).where(sync.TABLE.c.id == claimed['id']).values(metadata=meta))
        db.commit()
    assert asyncio.run(Reader(stand).once())
    with Session(stand) as db:
        row = db.execute(select(sync.TABLE).where(sync.TABLE.c.id == claimed['id'])).mappings().one()
        assert row['metadata']['attempts'] == 2 and row['status'] == 'manual_required'
        assert item['job_id'] == str(row['id'])


def test_due_filter_precedes_limit_so_future_retries_cannot_starve_other_jobs(stand):
    with Session(stand) as db:
        for number in range(50):
            db.execute(sync.insert(sync.TABLE).values(**sync._job_values(PROJECT_ID, uuid4(), sync.HINT,
                {'external_project_id': '99', 'page_id': str(number), 'next_attempt_at': (NOW + timedelta(hours=1)).isoformat()},
                NOW - timedelta(minutes=1))).values(status='read_retry'))
        db.commit()
    ready = enqueue(stand)
    claimed = Reader(stand)._claim()
    assert claimed and str(claimed['id']) == ready['job_id']


@pytest.mark.parametrize('change', ['expiry', 'binding'])
def test_late_result_is_not_accepted_after_lease_or_binding_change(stand, change):
    item = enqueue(stand)
    class ChangedDuringRead(Reader):
        async def _read(self, project_id, credentials, method, page_id=None):
            result = await super()._read(project_id, credentials, method, page_id)
            if method == 'getpage':
                with Session(stand) as db:
                    if change == 'expiry':
                        row = db.query(DeploymentLog).filter(DeploymentLog.id == item['job_id']).one()
                        row.meta = {**row.meta, 'lease_until': (NOW - timedelta(seconds=1)).isoformat()}
                    else:
                        integration = db.query(ProjectIntegration).one()
                        integration.details = {**integration.details, 'page_mappings': {'https://example.com/other': '123'}}
                    db.commit()
            return result
    asyncio.run(ChangedDuringRead(stand).once())
    with Session(stand) as db:
        row = db.query(DeploymentLog).filter(DeploymentLog.id == item['job_id']).one()
        assert row.status != 'manual_required' and row.changes == {}


def test_cached_source_is_invalidated_by_binding_change(stand):
    enqueue(stand)
    asyncio.run(Reader(stand).once())
    with Session(stand) as db:
        assert sync.cached_jobs(db, PROJECT_ID)[0]['status'] == 'manual_required'
        integration = db.query(ProjectIntegration).one()
        integration.details = {**integration.details, 'page_mappings': {'https://example.com/new': '123'}}
        db.commit()
        assert sync.cached_jobs(db, PROJECT_ID) == []


@pytest.mark.parametrize('count', [0, 67])
def test_cached_directory_distinguishes_unavailable_from_empty_and_pages_without_network(stand, count):
    with Session(stand) as db:
        assert sync.cached_pages(db, PROJECT_ID)['total'] is None
        item = sync.enqueue_catalogue(db, PROJECT_ID, approvals.ALICE)
    class CatalogueReader(Reader):
        async def _read(self, project_id, credentials, method, page_id=None):
            if method == 'getpageslist':
                self.calls.append(method)
                return [{'id': str(i), 'projectid': '42', 'filename': f'page-{i}'} for i in reversed(range(1, count + 1))]
            return await super()._read(project_id, credentials, method, page_id)
    reader = CatalogueReader(stand)
    asyncio.run(reader.once())
    with Session(stand) as db:
        first = sync.cached_pages(db, PROJECT_ID)
        second = sync.cached_pages(db, PROJECT_ID, offset=50)
        assert first['status'] == 'cached' and first['total'] == second['total'] == count
        assert [page['page_id'] for page in first['items'] + second['items']] == [str(i) for i in range(1, count + 1)]
        assert len(first['items']) == min(50, count)
        assert reader.calls == ['getprojectinfo', 'getpageslist']
        assert db.query(DeploymentLog).filter(DeploymentLog.id == item['job_id']).one().applied_at is None
        integration = db.query(ProjectIntegration).one()
        integration.details = {**integration.details, 'page_mappings': {'https://example.com/new': '123'}}
        db.commit()
        assert sync.cached_pages(db, PROJECT_ID)['total'] is None


@pytest.mark.parametrize('kind', ['quota', 'hint'])
def test_runtime_timestamp_is_taken_after_advisory_lock_wait(stand, monkeypatch, kind):
    fingerprint = sync._fingerprint('public-fixture')
    domain = 'tilda-budget/' + fingerprint if kind == 'quota' else 'tilda-hint/' + PROJECT_ID
    original_lock = sync._lock
    entered = Event()
    def observed_lock(db, name):
        entered.set()
        original_lock(db, name)
    with Session(stand) as holder, ThreadPoolExecutor(max_workers=1) as pool:
        original_lock(holder, domain)
        monkeypatch.setattr(sync, '_lock', observed_lock)
        def write():
            with Session(stand) as db:
                if kind == 'quota':
                    sync.reserve_read(db, PROJECT_ID, fingerprint, 'getpage')
                else:
                    sync.enqueue_hint(db, hint())
        future = pool.submit(write)
        assert entered.wait(5)
        time.sleep(.05)
        released_after = holder.scalar(select(sync.func.clock_timestamp()))
        holder.commit()
        future.result(timeout=5)
    with Session(stand) as db:
        row = db.query(DeploymentLog).one()
        assert row.created_at >= released_after


@pytest.mark.parametrize('scenario', ['match', 'mismatch', 'stale_mapping', 'entity_mismatch', 'binding_changed', 'revoked', 'network', 'expired'])
def test_manual_verification_is_independent_version_bound_and_never_a_cms_receipt(stand, scenario):
    from shared.hitl_verification import PublicFacts, PublicObservation
    from shared.tilda_contract import ManualCheckRequest
    from services.management_service.api.endpoints.hitl import _serialize_approval
    with Session(stand) as db:
        task = db.get(Task, sync.UUID(TASK_ID))
        task.task_type = TaskType.UPDATE_TILDA_PAGE
        approval = db.query(HITLApproval).one()
        version = approval.diff_data['recommendation_version']
        if scenario == 'entity_mismatch':
            from services.management_service.proposal_handoff import version_diff
            from services.semantic_service.db.models import RecommendationVersionRow
            from services.semantic_service.proposals.model import ExternalEntity
            from services.semantic_service.proposals.repository import row_from_version
            from tests.fixtures.management_proposal import management_proposal_fixture
            _, _, updated = management_proposal_fixture(entity=ExternalEntity(kind='tilda_page', id='999'),
                evidence_override=version['evidence'])
            db.query(RecommendationVersionRow).delete()
            db.add(row_from_version(updated))
            approval.diff_data = version_diff(updated)
            version = updated.model_dump(mode='json')
        from services.management_service.approval_policy import DecisionRequest
        decision = DecisionRequest(approval_id=str(approval.id), recommendation_version_id=version['version_id'],
                                   content_hash=version['content_hash'])
        db.commit()
        ApprovalService(db).decide(TASK_ID, UserActor(approvals.ALICE), decision, 'approve')
    queued = enqueue(stand)
    asyncio.run(Reader(stand).once())
    with Session(stand) as db:
        row = db.query(DeploymentLog).filter(DeploymentLog.id == queued['job_id']).one()
        row.changes = {**row.changes, 'pages': [{'page_id': '123', 'url': TARGET_URL}]}
        db.commit()
        if scenario == 'stale_mapping':
            integration = db.query(ProjectIntegration).one()
            integration.details = {**integration.details, 'page_mappings': {'https://example.com/elsewhere': '123'}}
            db.commit()
    async def authority():
        with Session(stand) as db:
            service = ApprovalService(db)
            approval, task = service.readable(TASK_ID, UserActor(approvals.ALICE))
            return {**_serialize_approval(approval, task),
                    'authorization_status': service.authorization_status(approval, task, UserActor(approvals.ALICE))}
    class PublicReader:
        calls = []
        async def read(self, url):
            self.calls.append(url)
            if scenario == 'network':
                from services.client_api_gateway.public_verifier import PublicReadError
                raise PublicReadError('fixture_failure')
            if scenario in {'binding_changed', 'revoked', 'expired'}:
                with Session(stand) as db:
                    if scenario == 'binding_changed':
                        integration = db.query(ProjectIntegration).one()
                        integration.details = {**integration.details, 'page_mappings': {'https://example.com/elsewhere': '123'}}
                    elif scenario == 'revoked':
                        ApprovalService(db).decide(TASK_ID, UserActor(approvals.ALICE), decision, 'revoke')
                    else:
                        row = db.query(DeploymentLog).filter(DeploymentLog.change_type == MANUAL).one()
                        row.meta = {**row.meta, 'deadline': (NOW - timedelta(seconds=1)).isoformat()}
                    db.commit()
            return PublicObservation(url=url, final_url=url, observed_at=NOW, status=200,
                headers={'content-type': 'text/html'}, body_sha256='a' * 64,
                facts=PublicFacts(titles=(version['changes'][0]['after'] if scenario != 'mismatch' else 'Mismatch',),
                    descriptions=(), jsonld=(), h1=(), links=()))
    request = ManualCheckRequest(check_id=str(uuid4()), approval_id=decision.approval_id,
        recommendation_version_id=decision.recommendation_version_id, content_hash=decision.content_hash)
    reader = PublicReader()
    with Session(stand) as db:
        if scenario in {'stale_mapping', 'entity_mismatch'}:
            with pytest.raises(sync.TildaSyncError, match='tilda_page_mapping_required'):
                asyncio.run(verify_manual(db, PROJECT_ID, TASK_ID, approvals.ALICE, request, authority, reader))
            assert reader.calls == []
            assert db.query(DeploymentLog).filter(DeploymentLog.change_type == MANUAL).count() == 0
            return
        result = asyncio.run(verify_manual(db, PROJECT_ID, TASK_ID, approvals.ALICE, request, authority, reader))
        expected = {'match': 'manual_verified', 'mismatch': 'manual_mismatch', 'expired': 'manual_check_unknown'}
        assert result['status'] == expected.get(scenario, 'manual_check_failed') and result['receipt'] is None
        if scenario not in {'binding_changed', 'revoked'}:
            repeated = asyncio.run(verify_manual(db, PROJECT_ID, TASK_ID, approvals.ALICE, request, authority, reader))
            assert repeated == result
        assert reader.calls == [TARGET_URL]
        db.expire_all()
        assert db.get(Task, sync.UUID(TASK_ID)).status != TaskStatus.COMPLETED
        assert db.query(DeploymentLog).filter(DeploymentLog.change_type == MANUAL).count() == 1
