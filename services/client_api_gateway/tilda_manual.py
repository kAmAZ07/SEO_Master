"""Independent manual comparison, never a CMS receipt or automatic verified state."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from services.client_api_gateway.public_verifier import PublicPageReader, PublicReadError
from services.client_api_gateway.tilda_sync import HINT, SYNC, TABLE, TildaSyncError, _lock, binding, fresh_time
from shared.hitl_verification import evaluate_public
from shared.projects.scope import parse_scope
from shared.tilda_contract import ManualCheckRequest

MANUAL = 'tilda_manual_check_v1'


def stored_check(db: Session, project_id: str, task_id: str, check_id: str) -> dict[str, Any]:
    row = db.execute(select(TABLE).where(TABLE.c.id == UUID(check_id), TABLE.c.project_id == project_id,
        TABLE.c.task_id == task_id, TABLE.c.change_type == MANUAL).with_for_update()).mappings().first()
    if row is None:
        raise TildaSyncError('manual_check_not_found', 404)
    state = row['status']
    if state == 'manual_checking' and datetime.fromisoformat(row['metadata']['deadline']) < datetime.now(timezone.utc):
        state = 'manual_check_unknown'
        db.execute(update(TABLE).where(TABLE.c.id == row['id']).values(status=state))
        db.commit()
    return {'check_id': check_id, 'status': state, 'project_id': project_id, 'task_id': task_id,
            'recommendation_version_id': row['metadata']['recommendation_version_id'],
            'content_hash': row['metadata']['content_hash'], 'result': row['changes'], 'receipt': None}


def public_contract(version: dict[str, Any]) -> dict[str, Any]:
    # This is a public predicate view, not a modified/signed RecommendationVersion.
    view = deepcopy({key: version[key] for key in ('target', 'changes', 'expected_checks')})
    fields: dict[str, dict[str, Any]] = {}
    paths = {'head.title': 'title', 'head.description': 'description', 'head.meta_description': 'description', 'head.jsonld': 'jsonld'}
    for change in view['changes']:
        if change['mode'] == 'unchanged':
            continue
        field = paths.get(change['path'])
        if field is None or field in fields:
            raise TildaSyncError('manual_predicate_unsupported')
        change['cms_field'] = field
        if change['path'] == 'head.meta_description':
            change['path'] = 'head.description'
        fields[field] = {'state': 'missing' if change['mode'] == 'remove' else 'value',
                         'value': change['after'] if change['mode'] == 'set' else None}
    for predicate in view['expected_checks']:
        if predicate.get('path') == 'head.meta_description':
            predicate['path'] = 'head.description'
    return {'version': view, 'after': {'fields': fields}}


async def verify_manual(db: Session, project_id: str, task_id: str, actor_id: str, command: ManualCheckRequest,
                        authority: Callable[[], Awaitable[dict[str, Any]]], reader: PublicPageReader | None = None) -> dict[str, Any]:
    credentials = binding(db, project_id)
    def approved(data: dict[str, Any]) -> dict[str, Any]:
        version = (data.get('diff_data') or {}).get('recommendation_version') or {}
        if (data.get('task_id') != task_id or data.get('project_id') != project_id or data.get('id') != command.approval_id
            or data.get('authorization_status') != 'approved' or version.get('version_id') != command.recommendation_version_id
            or version.get('content_hash') != command.content_hash):
            raise TildaSyncError('manual_approval_changed')
        return version
    version = approved(await authority())
    target = version['target']['url']
    entity = version['target'].get('entity')
    if entity is not None and entity.get('kind') != 'tilda_page':
        raise TildaSyncError('manual_target_unsupported')
    if not parse_scope(credentials['origin'] + credentials['path_prefix']).contains(target):
        raise TildaSyncError('tilda_scope_mismatch')
    rows = db.execute(select(TABLE.c.changes).where(TABLE.c.project_id == project_id,
        TABLE.c.change_type.in_([HINT, SYNC]), TABLE.c.status == 'manual_required',
        TABLE.c['metadata']['binding_revision'].astext == credentials['binding_revision'],
        TABLE.c['metadata']['fingerprint'].astext == credentials['fingerprint'],
        TABLE.c['metadata']['external_project_id'].astext == str(credentials['project_id'])).order_by(TABLE.c.created_at.desc()).limit(50)).scalars()
    if not any(any(page.get('url') == target and (entity is None or page.get('page_id') == entity['id'])
                   for page in result.get('pages', [])) for result in rows):
        raise TildaSyncError('tilda_page_mapping_required')
    contract = public_contract(version)
    _lock(db, 'tilda-manual/' + project_id)
    now = fresh_time(db)
    existing = db.execute(select(TABLE).where(TABLE.c.id == UUID(command.check_id))).mappings().first()
    if existing is not None:
        meta = existing['metadata'] or {}
        if (existing['change_type'] != MANUAL or existing['project_id'] != project_id or existing['task_id'] != task_id
                or meta.get('actor_id') != actor_id or meta.get('content_hash') != command.content_hash
                or meta.get('recommendation_version_id') != command.recommendation_version_id
                or meta.get('approval_id') != command.approval_id):
            raise TildaSyncError('manual_check_conflict')
        return stored_check(db, project_id, task_id, command.check_id)
    recent = db.scalar(select(TABLE.c.id).where(TABLE.c.project_id == project_id, TABLE.c.task_id == task_id,
        TABLE.c.change_type == MANUAL, TABLE.c.created_at > now - timedelta(seconds=5)).limit(1))
    if recent:
        raise TildaSyncError('manual_check_rate_limited', 429, 5)
    minute = db.scalar(select(func.count()).select_from(TABLE).where(TABLE.c.project_id == project_id,
        TABLE.c.change_type == MANUAL, TABLE.c.created_at > now - timedelta(minutes=1)))
    if minute is not None and minute >= 20:
        raise TildaSyncError('manual_check_rate_limited', 429, 60)
    db.execute(insert(TABLE).values(id=UUID(command.check_id), project_id=project_id, task_id=task_id,
        entity_id=target, entity_type='tilda_manual_observation', change_type=MANUAL, status='manual_checking',
        metadata={**command.model_dump(), 'actor_id': actor_id, 'deadline': (now + timedelta(seconds=30)).isoformat()}, changes={}))
    db.commit()
    result: dict[str, Any] = {}
    state = 'manual_check_failed'
    try:
        observation = await (reader or PublicPageReader()).read(target)
        approved(await authority())  # A changed/revoked approval cannot become a successful comparison.
        if binding(db, project_id)['binding_revision'] != credentials['binding_revision']:
            raise TildaSyncError('manual_binding_changed')
        check = evaluate_public(contract, observation)
        state = 'manual_verified' if check.matches else 'manual_mismatch'
        result = check.model_dump(mode='json')
    except PublicReadError:
        result = {'error_code': 'manual_public_read_failed'}
    except TildaSyncError as error:
        result = {'error_code': error.code}
    row = db.execute(select(TABLE).where(TABLE.c.id == UUID(command.check_id)).with_for_update()).mappings().one()
    if datetime.fromisoformat(row['metadata']['deadline']) < datetime.now(timezone.utc):
        state, result = 'manual_check_unknown', {}
    db.execute(update(TABLE).where(TABLE.c.id == row['id']).values(status=state, changes=result))
    db.commit()
    return stored_check(db, project_id, task_id, command.check_id)
