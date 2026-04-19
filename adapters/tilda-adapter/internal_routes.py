import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Header, HTTPException, status
from pydantic import BaseModel, Field

from config import settings
from meta_injector import TildaMetaInjector
from tilda_api_client import TildaAPIClient

router = APIRouter(prefix='/internal/tilda', tags=['internal'])


class PatchOp(BaseModel):
    op: Literal['add', 'replace', 'remove', 'move', 'copy', 'test']
    path: str
    value: Optional[Any] = None


class TildaPatchRequest(BaseModel):
    project_id: str = Field(..., min_length=1)
    page_id: str = Field(..., min_length=1)
    changes: List[PatchOp] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    credentials: Dict[str, str] = Field(default_factory=dict)


def _require_internal_key(x_internal_api_key: str = Header(..., alias='X-Internal-API-Key')) -> None:
    if not settings.internal_api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail='TILDA_INTERNAL_API_KEY is not configured',
        )
    if x_internal_api_key != settings.internal_api_key:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Invalid internal API key')


def _value_from_paths(changes: List[PatchOp], path_candidates: List[str]) -> Any:
    normalized = {candidate.lower() for candidate in path_candidates}
    for change in changes:
        if change.op not in ('add', 'replace'):
            continue
        if change.path.lower() in normalized:
            return change.value
    return None


def _page_result(page_response: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(page_response, dict):
        return {}

    result = page_response.get('result')
    if isinstance(result, dict):
        return result
    return page_response


def _first_present(source: Dict[str, Any], keys: List[str]) -> Any:
    for key in keys:
        value = source.get(key)
        if value not in (None, ''):
            return value
    return None


def _compact_raw_page(result: Dict[str, Any]) -> Dict[str, Any]:
    allowed_keys = [
        'id',
        'pageid',
        'projectid',
        'title',
        'descr',
        'description',
        'alias',
        'filename',
        'published',
        'date',
        'updated',
        'img',
        'featureimg',
    ]
    raw = {key: result[key] for key in allowed_keys if key in result}
    html_value = result.get('html') or result.get('body') or result.get('content')
    if isinstance(html_value, str):
        raw['html_length'] = len(html_value)
        raw['html_hash'] = hashlib.sha256(html_value.encode('utf-8')).hexdigest()
    return raw


def extract_tilda_page_snapshot(page_response: Dict[str, Any]) -> Dict[str, Any]:
    result = _page_result(page_response)
    page_id = _first_present(result, ['pageid', 'id', 'page_id'])
    title = _first_present(result, ['title', 'name'])
    description = _first_present(result, ['descr', 'description', 'meta_description'])
    alias = _first_present(result, ['alias', 'filename', 'slug'])
    page_url = _first_present(result, ['url', 'published_url', 'link', 'page_url'])

    snapshot: Dict[str, Any] = {
        'page_id': str(page_id) if page_id is not None else None,
        'title': title,
        'description': description,
        'alias': alias,
        'page_url': page_url,
        'published': _first_present(result, ['published', 'is_published']),
        'updated_at': _first_present(result, ['updated', 'date', 'modified']),
        'source_status': page_response.get('status') if isinstance(page_response, dict) else None,
        'raw': _compact_raw_page(result),
    }
    return {key: value for key, value in snapshot.items() if value not in (None, {}, [])}


def _previous_snapshot(webhook_payload: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(webhook_payload, dict):
        return {}
    for key in ('previous', 'before', 'old', 'old_page', 'previous_page'):
        value = webhook_payload.get(key)
        if isinstance(value, dict):
            return value
    return {}


def _stable_change_set_id(project_id: str, page_id: str, event: str, snapshot: Dict[str, Any]) -> str:
    raw = json.dumps(
        {
            'project_id': project_id,
            'page_id': page_id,
            'event': event,
            'snapshot': snapshot,
        },
        ensure_ascii=True,
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()[:24]


def build_tilda_change_set(
    *,
    project_id: str,
    page_id: str,
    event: str,
    webhook_payload: Dict[str, Any],
    page_response: Dict[str, Any],
    page_url: Optional[str] = None,
) -> Dict[str, Any]:
    snapshot = extract_tilda_page_snapshot(page_response)
    if not snapshot.get('page_id'):
        snapshot['page_id'] = page_id
    if page_url and not snapshot.get('page_url'):
        snapshot['page_url'] = page_url

    previous = _previous_snapshot(webhook_payload)
    fields = {
        'title': ('title', 'name'),
        'description': ('description', 'descr', 'meta_description'),
        'page_url': ('page_url', 'url', 'published_url', 'link'),
        'alias': ('alias', 'filename', 'slug'),
    }

    changes: List[Dict[str, Any]] = []
    for field, previous_keys in fields.items():
        after_value = snapshot.get(field)
        if after_value in (None, ''):
            continue
        before_value = _first_present(previous, list(previous_keys)) if previous else None
        if previous and before_value == after_value:
            continue
        changes.append(
            {
                'op': 'replace' if previous else 'upsert',
                'path': f'/tilda/page/{field}',
                'before': before_value,
                'after': after_value,
                'source': 'tilda_api',
            }
        )

    if not changes:
        changes.append(
            {
                'op': 'snapshot',
                'path': '/tilda/page',
                'before': previous or None,
                'after': snapshot,
                'source': 'tilda_api',
            }
        )

    return {
        'change_set_id': _stable_change_set_id(project_id, page_id, event, snapshot),
        'source': 'tilda_webhook',
        'event': event,
        'project_id': project_id,
        'page_id': page_id,
        'page_url': snapshot.get('page_url') or page_url,
        'created_at': datetime.now(timezone.utc).isoformat(),
        'changes': changes,
        'before': previous,
        'after': snapshot,
        'metadata': {
            'has_previous_snapshot': bool(previous),
            'source_status': snapshot.get('source_status'),
            'raw_keys': sorted((snapshot.get('raw') or {}).keys()),
        },
    }


def _collect_interlinks(changes: List[PatchOp]) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    for change in changes:
        if change.op not in ('add', 'replace'):
            continue
        if '/internal_links' not in change.path.lower():
            continue

        value = change.value
        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    result.append(item)
        elif isinstance(value, dict):
            result.append(value)

    return result


def _build_injector(credentials: Dict[str, str]) -> TildaMetaInjector:
    public_key = str(credentials.get('public_key') or '').strip()
    secret_key = str(credentials.get('secret_key') or '').strip()
    if not public_key or not secret_key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='Tilda credentials must be provided per request',
        )

    return TildaMetaInjector(
        client=TildaAPIClient(
            public_key=public_key,
            secret_key=secret_key,
        )
    )


def _wrap_result(page_id: str, result: Dict[str, Any], extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    warnings = result.get('warnings') if isinstance(result, dict) else None
    raw_status = str(result.get('status') or '') if isinstance(result, dict) else ''

    if raw_status in {'requires_hitl', 'blocked', 'skipped', 'failed'}:
        status_value = raw_status
    elif warnings:
        status_value = 'warning'
    else:
        status_value = 'ok'

    response: Dict[str, Any] = {
        'status': status_value,
        'page_id': page_id,
        'result': result,
    }
    if warnings:
        response['warnings'] = warnings
    if extra:
        response.update(extra)
    return response


@router.post('/meta')
async def apply_meta_patch(
    payload: TildaPatchRequest,
    x_internal_api_key: str = Header(..., alias='X-Internal-API-Key'),
):
    _require_internal_key(x_internal_api_key)
    injector = _build_injector(payload.credentials)

    title = _value_from_paths(payload.changes, ['/title', '/meta_title'])
    description = _value_from_paths(payload.changes, ['/description', '/meta_description'])
    h1 = _value_from_paths(payload.changes, ['/h1'])

    result = await injector.apply_meta(
        payload.page_id,
        {
            'title': title,
            'description': description,
            'h1': h1,
        },
    )

    return _wrap_result(payload.page_id, result)


@router.post('/schema')
async def apply_schema_patch(
    payload: TildaPatchRequest,
    x_internal_api_key: str = Header(..., alias='X-Internal-API-Key'),
):
    _require_internal_key(x_internal_api_key)
    injector = _build_injector(payload.credentials)

    schema_value = _value_from_paths(payload.changes, ['/schema', '/schema_org', '/jsonld'])
    if schema_value is None:
        return {'status': 'skipped', 'reason': 'no_schema_change'}

    result = await injector.apply_schema(payload.page_id, str(schema_value))
    return _wrap_result(payload.page_id, result)


@router.post('/interlinks')
async def apply_interlinks_patch(
    payload: TildaPatchRequest,
    x_internal_api_key: str = Header(..., alias='X-Internal-API-Key'),
):
    _require_internal_key(x_internal_api_key)
    injector = _build_injector(payload.credentials)

    links = _collect_interlinks(payload.changes)
    result = await injector.apply_interlinks(payload.page_id, links)
    return _wrap_result(payload.page_id, result, {'links_count': len(links)})
