"""Read-only Tilda inbox and quota journal in existing migrated Client storage."""
import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
from typing import Any, Callable, cast
from urllib.parse import urlsplit
from uuid import UUID, NAMESPACE_URL, uuid4, uuid5

import httpx
from sqlalchemy import DateTime, and_, func, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from services.client_api_gateway.config import settings
from services.client_api_gateway import db as client_db
from services.client_api_gateway.models import DeploymentLog
from services.project_integrations import IntegrationNotFoundError, IntegrationsService
from shared.projects.scope import parse_scope
from shared.tilda_contract import PublishHint, ReadMethod

HINT = 'tilda_publish_hint_v1'
SYNC = 'tilda_catalogue_v1'
BUDGET = 'tilda_read_budget_v1'
TABLE = DeploymentLog.__table__
ERRORS = frozenset({'tilda_quota_exceeded', 'tilda_upstream_unavailable', 'tilda_upstream_error',
    'tilda_upstream_invalid_response', 'tilda_credentials_rejected', 'tilda_read_disabled', 'tilda_scope_mismatch'})


class TildaSyncError(ValueError):
    def __init__(self, code: str, status: int = 409, retry_after: int = 0):
        self.code, self.status, self.retry_after = code, status, retry_after
        super().__init__(code)


def _fingerprint(public_key: str) -> str:
    return hashlib.sha256(public_key.encode()).hexdigest()


def _lock(db: Session, domain: str) -> None:
    db.execute(text("SET LOCAL lock_timeout='1s'"))
    db.execute(text('SELECT pg_advisory_xact_lock(:id)'),
               {'id': int.from_bytes(hashlib.sha256(domain.encode()).digest()[:8], 'big', signed=True)})


def binding(db: Session, project_id: str) -> dict[str, Any]:
    row = db.execute(text('SELECT id, origin, path_prefix, status FROM audit_schema.projects WHERE id=:id'), {'id': project_id}).mappings().first()
    if row is None or row['status'] != 'active':
        raise TildaSyncError('tilda_binding_not_found', 404)
    try:
        credentials = IntegrationsService().get_tilda_credentials(db, project_id)
    except IntegrationNotFoundError:
        raise TildaSyncError('tilda_binding_not_found', 404) from None
    value = {**dict(row), **credentials, 'fingerprint': _fingerprint(credentials['public_key'])}
    material = {key: value[key] for key in ('origin', 'path_prefix', 'project_id', 'public_key', 'secret_key', 'page_mappings')}
    value['binding_revision'] = hashlib.sha256(json.dumps(material, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return value


def fresh_time(db: Session, explicit: datetime | None = None) -> datetime:
    value = explicit or db.scalar(select(func.clock_timestamp()))
    if not isinstance(value, datetime):
        raise TildaSyncError('tilda_store_clock_unavailable', 503)
    return value


def callback_token(project_id: str, external_id: str, public_key: str) -> str:
    secret = settings.TILDA_WEBHOOK_SECRET or ''
    if len(secret) < 32:
        raise TildaSyncError('tilda_callback_unconfigured', 503)
    message = 'seo-master/tilda-callback/v1\x00' + project_id + '\x00' + external_id + '\x00' + public_key
    return hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()


def callback_url(project_id: str, credentials: dict[str, Any]) -> str | None:
    base = settings.TILDA_WEBHOOK_PUBLIC_URL or ''
    parsed = urlsplit(base)
    if not base:
        return None
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path != '/api/tilda/webhook':
        raise TildaSyncError('tilda_callback_url_invalid', 503)
    return base + '/' + project_id + '/' + callback_token(project_id, str(credentials['project_id']), credentials['public_key']) + '/published'


def _job_values(project_id: str, job_id: UUID, kind: str, metadata: dict[str, Any], now: datetime) -> dict[str, Any]:
    return {'id': job_id, 'project_id': project_id, 'change_type': kind, 'entity_type': 'tilda_read',
            'entity_id': metadata.get('page_id') or str(metadata['external_project_id']), 'status': 'queued',
            'changes': {}, 'metadata': metadata, 'created_at': now}


def enqueue_hint(db: Session, hint: PublishHint, *, now: datetime | None = None) -> dict[str, str]:
    try:
        credentials = binding(db, hint.binding)
        if (not hmac.compare_digest(hint.publickey.encode(), credentials['public_key'].encode())
            or str(credentials['project_id']) != hint.projectid
            or not hmac.compare_digest(hint.token, callback_token(hint.binding, hint.projectid, hint.publickey))):
            raise TildaSyncError('tilda_binding_not_found', 404)
        job_id = uuid5(NAMESPACE_URL, 'seo-master/tilda-hint/v1/' + '/'.join((hint.binding, hint.projectid, hint.pageid, hint.published)))
        _lock(db, 'tilda-hint/' + hint.binding)
        now = fresh_time(db, now)
        stamp = int(hint.published)
        if not int((now - timedelta(days=30)).timestamp()) <= stamp <= int(now.timestamp()) + 300:
            raise TildaSyncError('tilda_hint_expired', 422)
        exists = db.scalar(select(TABLE.c.id).where(TABLE.c.id == job_id))
        if exists is not None:
            db.commit()
            return {'status': 'duplicate', 'job_id': str(job_id)}
        recent = db.scalar(select(func.count()).select_from(TABLE).where(TABLE.c.project_id == hint.binding,
            TABLE.c.change_type == HINT, TABLE.c.created_at > now - timedelta(minutes=1)))
        if recent is not None and recent >= 30:
            raise TildaSyncError('tilda_hint_rate_limited', 429, 60)
        metadata = {'external_project_id': hint.projectid, 'page_id': hint.pageid,
                    'published': hint.published, 'fingerprint': credentials['fingerprint'],
                    'binding_revision': credentials['binding_revision'], 'attempts': 0}
        db.execute(insert(TABLE).values(**_job_values(hint.binding, job_id, HINT, metadata, now)).on_conflict_do_nothing(index_elements=['id']))
        db.commit()  # No network fetch or ownership/mapping mutation before this ACK.
        return {'status': 'queued', 'job_id': str(job_id)}
    except Exception:
        db.rollback()
        raise


def enqueue_catalogue(db: Session, project_id: str, actor_id: str) -> dict[str, str]:
    credentials = binding(db, project_id)
    _lock(db, 'tilda-hint/' + project_id)
    now = fresh_time(db)
    recent = db.scalar(select(TABLE.c.id).where(TABLE.c.project_id == project_id, TABLE.c.change_type == SYNC,
        TABLE.c.created_at > now - timedelta(seconds=30)).order_by(TABLE.c.created_at.desc()).limit(1))
    if recent is not None:
        db.commit()
        return {'status': 'duplicate', 'job_id': str(recent)}
    job_id = uuid4()
    db.execute(insert(TABLE).values(**_job_values(project_id, job_id, SYNC, {
        'external_project_id': str(credentials['project_id']), 'fingerprint': credentials['fingerprint'],
        'binding_revision': credentials['binding_revision'], 'attempts': 0, 'actor_id': actor_id}, now)))
    db.commit()
    return {'status': 'queued', 'job_id': str(job_id)}


def cached_jobs(db: Session, project_id: str) -> list[dict[str, Any]]:
    current = binding(db, project_id)
    rows = db.execute(select(TABLE).where(TABLE.c.project_id == project_id, TABLE.c.change_type.in_([HINT, SYNC]),
        TABLE.c['metadata']['binding_revision'].astext == current['binding_revision']).order_by(
        TABLE.c.created_at.desc(), TABLE.c.id.desc()).limit(5)).mappings()
    return [{'job_id': str(row['id']), 'status': row['status'], 'created_at': row['created_at'].isoformat(),
             'result': {**row['changes'], 'pages': row['changes'].get('pages', [])[:50],
                        'page_count': len(row['changes'].get('pages', []))}, 'error_code': row['error_message'],
             'attempts': row['metadata'].get('attempts', 0)} for row in rows]


def cached_pages(db: Session, project_id: str, limit: int = 50, offset: int = 0) -> dict[str, Any]:
    if not 1 <= limit <= 100 or not 0 <= offset <= 10000:
        raise TildaSyncError('tilda_page_limit_invalid', 422)
    current = binding(db, project_id)
    row = db.execute(select(TABLE.c.changes).where(TABLE.c.project_id == project_id, TABLE.c.change_type == SYNC,
        TABLE.c.status == 'manual_required', TABLE.c['metadata']['binding_revision'].astext == current['binding_revision'])
        .order_by(TABLE.c.created_at.desc(), TABLE.c.id.desc()).limit(1)).scalar()
    if row is None:
        return {'project_id': project_id, 'status': 'unavailable', 'items': [], 'total': None, 'observed_at': None}
    pages = sorted(row['pages'], key=lambda item: (int(item['page_id']), item['url']))
    return {'project_id': project_id, 'status': 'cached', 'items': pages[offset:offset + limit],
            'total': len(pages), 'observed_at': row['observed_at']}


def reserve_read(db: Session, project_id: str, fingerprint: str, method: ReadMethod, *, now: datetime | None = None) -> None:
    _lock(db, 'tilda-budget/' + fingerprint)
    now = fresh_time(db, now)
    used = db.scalar(select(func.count()).select_from(TABLE).where(TABLE.c.change_type == BUDGET,
        TABLE.c['metadata']['fingerprint'].astext == fingerprint, TABLE.c.created_at > now - timedelta(hours=1)))
    if used is not None and used >= settings.TILDA_REQUESTS_PER_HOUR:
        raise TildaSyncError('tilda_quota_exceeded', 429, 3600)
    db.execute(insert(TABLE).values(id=uuid4(), project_id=project_id, entity_id=method, entity_type='tilda_api_read',
        change_type=BUDGET, status='read_reserved', changes={}, metadata={'fingerprint': fingerprint}, created_at=now))
    db.commit()  # Reservation stays spent even if the process dies before the response.


class TildaReader:
    def __init__(self, session_factory: Callable[[], Session] | None = None):
        self.sessions = session_factory or self._runtime_session

    @staticmethod
    def _runtime_session() -> Session:
        factory = cast(Callable[[], Session] | None, client_db.SessionLocal)
        if factory is None:
            raise TildaSyncError('tilda_store_unconfigured', 503)
        return factory()

    async def _read(self, project_id: str, credentials: dict[str, Any], method: ReadMethod, page_id: str | None = None) -> Any:
        def reserve() -> None:
            with self.sessions() as db:
                current = binding(db, project_id)
                if current['binding_revision'] != credentials['binding_revision']:
                    raise TildaSyncError('tilda_binding_changed')
                reserve_read(db, project_id, current['fingerprint'], method)
        await asyncio.to_thread(reserve)
        try:
            async with httpx.AsyncClient(timeout=22, trust_env=False, follow_redirects=False) as client:
                response = await client.post((settings.TILDA_ADAPTER_URL or '').rstrip('/') + '/internal/tilda/read',
                    headers={'X-Internal-API-Key': settings.TILDA_INTERNAL_API_KEY or ''}, json={
                        'method': method, 'public_key': credentials['public_key'], 'secret_key': credentials['secret_key'],
                        'external_project_id': str(credentials['project_id']), 'page_id': page_id})
        except httpx.HTTPError:
            raise TildaSyncError('tilda_upstream_unavailable', 503) from None
        try:
            data = response.json()
            if response.status_code != 200:
                code = data.get('detail')
                raise TildaSyncError(code if code in ERRORS else 'tilda_upstream_invalid_response',
                    response.status_code, 3600 if code == 'tilda_quota_exceeded' else 0)
            if data['status'] != 'FOUND':
                raise ValueError()
            return data['result']
        except (ValueError, KeyError, TypeError) as error:
            if isinstance(error, TildaSyncError):
                raise
            raise TildaSyncError('tilda_upstream_invalid_response', 502) from None

    def _claim(self) -> dict[str, Any] | None:
        now = datetime.now(timezone.utc)
        with self.sessions() as db:
            db.execute(text("SET LOCAL lock_timeout='1s'"))
            now = fresh_time(db)
            due = or_(TABLE.c.status == 'queued',
                and_(TABLE.c.status == 'reading', TABLE.c['metadata']['lease_until'].astext.cast(DateTime(timezone=True)) <= now),
                and_(TABLE.c.status == 'read_retry', TABLE.c['metadata']['next_attempt_at'].astext.cast(DateTime(timezone=True)) <= now))
            rows = db.execute(select(TABLE).where(TABLE.c.change_type.in_([HINT, SYNC]),
                due).order_by(TABLE.c.created_at, TABLE.c.id).limit(50)
                .with_for_update(skip_locked=True)).mappings()
            for row in rows:
                meta = dict(row['metadata'])
                if datetime.fromisoformat(meta.get('next_attempt_at') or meta.get('lease_until') or now.isoformat()) > now:
                    continue
                attempts = int(meta.get('attempts', 0))
                if attempts >= 3:
                    db.execute(update(TABLE).where(TABLE.c.id == row['id']).values(status='read_failed', error_message='tilda_read_budget_exhausted'))
                    continue
                meta.update(attempts=attempts + 1, lease_id=str(uuid4()), lease_until=(now + timedelta(seconds=90)).isoformat())
                meta.pop('next_attempt_at', None)
                db.execute(update(TABLE).where(TABLE.c.id == row['id']).values(status='reading', metadata=meta))
                db.commit()
                return {**dict(row), 'metadata': meta}
            db.commit()
            return None

    async def once(self) -> bool:
        job = await asyncio.to_thread(self._claim)
        if job is None:
            return False
        result: dict[str, Any] = {}
        code = None
        retry_after = 0
        try:
            def credentials_for_job() -> dict[str, Any]:
                with self.sessions() as db:
                    return binding(db, job['project_id'])
            credentials = await asyncio.to_thread(credentials_for_job)
            if (credentials['binding_revision'] != job['metadata'].get('binding_revision')
                    or credentials['fingerprint'] != job['metadata']['fingerprint']
                    or str(credentials['project_id']) != job['metadata']['external_project_id']):
                raise TildaSyncError('tilda_binding_changed')
            info = await self._read(job['project_id'], credentials, 'getprojectinfo')
            if not isinstance(info, dict) or str(info.get('id')) != str(credentials['project_id']):
                raise TildaSyncError('tilda_scope_mismatch')
            page_id = job['metadata'].get('page_id')
            pages = [await self._read(job['project_id'], credentials, 'getpage', page_id)] if page_id else await self._read(job['project_id'], credentials, 'getpageslist')
            scope = parse_scope(credentials['origin'] + credentials['path_prefix'])
            domain = str(info.get('customdomain') or '').strip().lower()
            domain_matches = domain == (urlsplit(credentials['origin']).hostname or '').lower()
            snapshots = []
            if not isinstance(pages, list) or len(pages) > 500:
                raise TildaSyncError('tilda_upstream_invalid_response')
            for page in pages:
                if not isinstance(page, dict) or str(page.get('projectid')) != str(credentials['project_id']):
                    raise TildaSyncError('tilda_scope_mismatch')
                identity = str(page.get('id', ''))
                if not identity.isascii() or not identity.isdigit() or len(identity) > 20:
                    raise TildaSyncError('tilda_upstream_invalid_response')
                url = None
                for mapped, mapped_id in credentials['page_mappings'].items():
                    if str(mapped_id) == identity and mapped.startswith(('https://', 'http://')) and scope.contains(mapped):
                        url = mapped
                        break
                filename = page.get('filename')
                if url is None and domain_matches and isinstance(filename, str) and filename:
                    candidate = credentials['origin'] + '/' + filename.lstrip('/')
                    if scope.contains(candidate):
                        url = candidate
                if not url:
                    continue
                snapshot = {key: page[key] for key in ('title', 'descr', 'published', 'filename') if key in page}
                for key, value in snapshot.items():
                    if value is not None and (not isinstance(value, str) or len(value) > (5000 if key == 'descr' else 2048)):
                        raise TildaSyncError('tilda_upstream_invalid_response')
                if isinstance(page.get('html'), str):
                    snapshot['html_sha256'] = hashlib.sha256(page['html'].encode()).hexdigest()
                snapshots.append({'page_id': identity, 'url': url, 'fields': snapshot})
            if not snapshots and (pages or not domain_matches):
                raise TildaSyncError('tilda_domain_mapping_required')
            result = {'source': 'tilda_authorized_read', 'observed_at': datetime.now(timezone.utc).isoformat(),
                      'pages': snapshots, 'write_status': 'write_unsupported', 'receipt': None}
        except TildaSyncError as error:
            code = error.code
            retry_after = error.retry_after or (15 if code == 'tilda_upstream_unavailable' else 0)
        except Exception:
            code = 'tilda_sync_unavailable'
            retry_after = 15
        def finish() -> None:
            with self.sessions() as db:
                current = db.execute(select(TABLE).where(TABLE.c.id == job['id']).with_for_update()).mappings().one()
                if current['metadata'].get('lease_id') != job['metadata']['lease_id']:
                    return  # A late result never overwrites a newer lease.
                if datetime.fromisoformat(current['metadata']['lease_until']) < fresh_time(db):
                    return
                try:
                    live = binding(db, job['project_id'])
                    unchanged = live['binding_revision'] == job['metadata'].get('binding_revision')
                except TildaSyncError:
                    unchanged = False
                final_code, final_result = (code, result) if unchanged else ('tilda_binding_changed', {})
                meta = dict(current['metadata'])
                meta.pop('lease_until', None)
                state = 'manual_required' if final_code is None else 'read_failed'
                if unchanged and final_code and retry_after and meta['attempts'] < 3:
                    state = 'read_retry'
                    meta['next_attempt_at'] = (datetime.now(timezone.utc) + timedelta(seconds=retry_after)).isoformat()
                db.execute(update(TABLE).where(TABLE.c.id == job['id']).values(status=state, metadata=meta,
                    changes=final_result, error_message=final_code))
                db.commit()
        await asyncio.to_thread(finish)
        return True
