from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Header, HTTPException, status
from pydantic import BaseModel, Field

from config import settings
from meta_injector import TildaMetaInjector

router = APIRouter(prefix='/internal/tilda', tags=['internal'])
injector = TildaMetaInjector()


class PatchOp(BaseModel):
    op: Literal['add', 'replace', 'remove', 'move', 'copy', 'test']
    path: str
    value: Optional[Any] = None


class TildaPatchRequest(BaseModel):
    project_id: str = Field(..., min_length=1)
    page_id: str = Field(..., min_length=1)
    changes: List[PatchOp] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


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

    links = _collect_interlinks(payload.changes)
    result = await injector.apply_interlinks(payload.page_id, links)
    return _wrap_result(payload.page_id, result, {'links_count': len(links)})
