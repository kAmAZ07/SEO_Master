"""DEC-04 on real migrated PostgreSQL: scoped atomic changes, races and pagination."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Barrier
from uuid import UUID

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from database.models import Project as CanonicalProject, Workspace, WorkspaceMember
from services.management_service.approval_policy import ApprovalError, UserActor
from services.management_service.models import Changelog, HITLApproval, Task, TaskStatus, TaskType
from services.management_service.priority_policy import PriorityChange, PriorityInputs, PriorityRecord, priority_key
from services.management_service.priority_service import PriorityService
from services.management_service.prioritizer import get_next_task, prioritize_project_tasks, priority_order
from tests.integration import test_management_approvals_postgres as approvals
from tests.integration.test_management_approvals_postgres import ALICE, BOB, WORKSPACE
from tests.integration.test_migrations_postgres import ADMIN_URL
from tests.fixtures.management_proposal import PROJECT_ID, TASK_ID, TARGET_URL

pytestmark = pytest.mark.skipif(not ADMIN_URL, reason='TEST_PG_ADMIN_URL is not set')
approval_stand = approvals.stand


@pytest.fixture()
def stand(approval_stand):
    engine, _, _ = approval_stand
    with Session(engine) as db:
        db.get(WorkspaceMember, (WORKSPACE, ALICE)).role = 'owner'
        db.commit()
    return engine


def command(revision=0, **changes):
    return PriorityChange.model_validate({'action': 'estimate', 'expected_revision': revision,
        'reason': 'Manual estimate, not a forecast', 'inputs': {'impact': .8, 'confidence': .75, 'effort': 2.}, **changes})


def change(engine, payload, user=ALICE):
    with Session(engine) as db:
        return PriorityService(db).change(TASK_ID, UserActor(user), payload)


def test_estimate_is_atomic_audited_and_does_not_approve_or_change_preview(stand):
    with Session(stand) as db:
        before = db.query(HITLApproval).one().diff_data
    result = change(stand, command())
    assert result['effective_score'] == pytest.approx(.3) and result['heuristic'] is True
    with Session(stand) as db:
        task = db.get(Task, UUID(TASK_ID))
        assert task.priority_score == pytest.approx(.3) and task.status == TaskStatus.PENDING
        assert db.query(HITLApproval).one().diff_data == before
        assert not db.query(HITLApproval).one().meta.get('decision_record')
        history = PriorityService(db).history(TASK_ID, UserActor(ALICE))
        assert len(history) == 1 and history[0]['actor_id'] == ALICE
        assert history[0]['before']['effective_score'] is None and history[0]['after'] == result
        assert history[0]['reason'] == command().reason


def test_zero_override_survives_recalculation_estimate_changes_and_clear_restores_formula(stand):
    change(stand, command())
    result = change(stand, command(1, action='override', score=0., inputs=None))
    assert result['computed_score'] == pytest.approx(.3) and result['effective_score'] == 0
    with Session(stand) as db:
        rows = prioritize_project_tasks(db, PROJECT_ID)
        assert rows[0].priority_score == 0
    result = change(stand, command(2, inputs={'impact': .4, 'confidence': 1., 'effort': 2.}))
    assert result['effective_score'] == 0 and result['computed_score'] == .2
    result = change(stand, command(3, action='clear_override', inputs=None))
    assert result['effective_score'] == .2 and result['override'] is None
    with Session(stand) as db:
        assert [row['revision'] for row in PriorityService(db).history(TASK_ID, UserActor(ALICE))] == [4, 3, 2, 1]


def test_unknown_is_not_zero_and_clear_does_not_invent_estimates(stand):
    change(stand, command(action='override', inputs=None, score=.9))
    result = change(stand, command(1, action='clear_override', inputs=None))
    assert result['computed_score'] is None and result['effective_score'] is None
    assert result['status'] == 'unknown'


@pytest.mark.parametrize('producer', ['crawl', 'score', 'scheduler'])
def test_background_producer_refreshes_stale_identity_map_and_keeps_override(stand, producer):
    with Session(stand) as stale:
        retained = stale.get(Task, UUID(TASK_ID))
        assert retained.meta.get('priority') is None
        change(stand, command(action='override', inputs=None, score=0.))
        if producer == 'crawl':
            from services.management_service.events.crawl_completed_handler import handle_crawl_completed_event
            handle_crawl_completed_event(stale, {'payload': {'task_id': TASK_ID, 'audit_id': 'audit-new'}})
        elif producer == 'score':
            from services.management_service.events.ff_score_recalculated_handler import handle_ff_score_recalculated_event
            from tests.unit.test_management_scores import event_payload
            event = event_payload(None, None)
            event['payload']['project_id'] = PROJECT_ID
            handle_ff_score_recalculated_event(stale, event)
        else:
            prioritize_project_tasks(stale, PROJECT_ID)
    with Session(stand) as db:
        task = db.get(Task, UUID(TASK_ID))
        assert task.meta['priority']['revision'] == 1
        assert task.meta['priority']['override']['score'] == 0
        assert task.priority_score == 0 and db.query(Changelog).count() == 1


@pytest.mark.parametrize('role', ['reviewer', 'viewer', 'deployer'])
def test_only_manage_role_may_change_priority(stand, role):
    with Session(stand) as db:
        db.get(WorkspaceMember, (WORKSPACE, ALICE)).role = role
        db.commit()
    with pytest.raises(ApprovalError) as error:
        change(stand, command())
    assert error.value.http_status == 403
    with Session(stand) as db:
        assert db.query(Changelog).count() == 0
        assert PriorityService(db).history(TASK_ID, UserActor(ALICE)) == []


def test_foreign_actor_cannot_change_or_read_journal(stand):
    for action in ('change', 'history'):
        with Session(stand) as db, pytest.raises(ApprovalError) as error:
            service = PriorityService(db)
            service.change(TASK_ID, UserActor(BOB), command()) if action == 'change' else service.history(TASK_ID, UserActor(BOB))
        assert error.value.http_status == 404


def test_same_revision_race_commits_one_change_and_one_history_entry(stand):
    barrier = Barrier(2)
    def worker():
        barrier.wait(timeout=10)
        try:
            return change(stand, command())['revision']
        except ApprovalError as error:
            return error.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: worker(), range(2)))
    assert sorted(map(str, results)) == ['1', 'priority_revision_conflict']
    with Session(stand) as db:
        assert db.query(Changelog).count() == 1


def test_failed_commit_rolls_back_both_priority_and_history(stand, monkeypatch):
    with Session(stand) as db:
        def fail():
            db.flush()
            raise RuntimeError('simulated_commit_failure')
        monkeypatch.setattr(db, 'commit', fail)
        with pytest.raises(RuntimeError, match='simulated_commit_failure'):
            PriorityService(db).change(TASK_ID, UserActor(ALICE), command())
    with Session(stand) as db:
        assert db.get(Task, UUID(TASK_ID)).meta.get('priority') is None
        assert db.query(Changelog).count() == 0


def test_sql_queue_matches_python_before_pagination_and_ignores_legacy_cache(stand):
    with Session(stand) as db:
        original = db.get(Task, UUID(TASK_ID))
        original.priority_score = 999  # Old cache is not a priority_v1 estimate.
        original.created_at = datetime(2026, 11, 15, tzinfo=timezone.utc)
        for number, impact, urgency in [(1, None, 'critical'), (2, .9, 'unknown'), (3, 0., 'unknown'), (4, .9, 'unknown')]:
            inputs = PriorityInputs(impact=impact, confidence=1., effort=1., urgency=urgency,
                                    urgency_reason='Triage' if urgency != 'unknown' else None)
            task = Task(id=UUID(int=number), project_id=UUID(PROJECT_ID), url=TARGET_URL,
                        task_type=TaskType.UPDATE_META, created_at=original.created_at,
                        meta={'priority': PriorityRecord(inputs=inputs).model_dump(mode='json')})
            task.calculate_priority()
            db.add(task)
        db.commit()
        all_rows = db.query(Task).all()
        expected = [row.id for row in sorted(all_rows, key=priority_key)]
        assert expected == [UUID(int=n) for n in (1, 2, 4, 3)] + [UUID(TASK_ID)]
        pages = [row.id for offset in (0, 2, 4) for row in db.query(Task).order_by(*priority_order()).offset(offset).limit(2).all()]
        assert pages == expected and get_next_task(db, PROJECT_ID).id == expected[0]
        # Generic user metadata cannot replace the protected priority contract.
        from services.management_service.schemas.task import TaskUpdate
        with pytest.raises(ValueError):
            TaskUpdate(metadata={'priority': {'override': {'actor_id': BOB}}})
        assert db.scalar(select(Changelog.id).limit(1)) is None
        assert db.scalar(text('SELECT count(*) FROM public.tasks')) == 5


def test_global_task_page_keeps_scope_for_different_owners_on_same_domain(stand):
    from services.management_service.api.endpoints.tasks import list_tasks
    own_project, foreign_project, foreign_workspace = (str(UUID(int=n)) for n in (100, 200, 300))
    with Session(stand) as db:
        db.add(Workspace(id=foreign_workspace, name='Other owner', owner_id=BOB, kind='personal'))
        db.flush()
        db.add(WorkspaceMember(workspace_id=foreign_workspace, user_id=BOB, role='owner'))
        for project_id, owner, workspace in [(own_project, ALICE, WORKSPACE), (foreign_project, BOB, foreign_workspace)]:
            prefix = '/shop/' if project_id == own_project else '/'
            db.add(CanonicalProject(id=project_id, name='Same domain', owner_id=owner, workspace_id=workspace,
                url='https://example.com' + prefix, origin='https://example.com', path_prefix=prefix, status='active'))
        db.flush()
        for number, project_id, impact in [(10, own_project, .1), (20, foreign_project, 1.)]:
            inputs = PriorityInputs(impact=impact, confidence=1., effort=1., urgency='critical', urgency_reason='Triage')
            task = Task(id=UUID(int=number), project_id=UUID(project_id),
                        url='https://example.com/shop/' if project_id == own_project else TARGET_URL, task_type=TaskType.UPDATE_META,
                        meta={'priority': PriorityRecord(inputs=inputs).model_dump(mode='json')})
            task.calculate_priority()
            db.add(task)
        db.commit()
        pages = [item for offset in (0, 1) for item in list_tasks(project_id=None, status_filter=None,
            limit=1, offset=offset, actor=UserActor(ALICE), db=db)]
        assert [item['id'] for item in pages] == [str(UUID(int=10)), TASK_ID]
        assert all(item['project_id'] != foreign_project for item in pages)
