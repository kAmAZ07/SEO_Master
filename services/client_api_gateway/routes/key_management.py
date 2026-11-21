"""Compatibility response for retired HTTP key administration."""

from fastapi import APIRouter, HTTPException


router = APIRouter(prefix='/api/client/keys', tags=['client-keys'], include_in_schema=False)


@router.get('/{project_id}')
@router.post('/{project_id}/rotate')
async def key_management_retired() -> None:
    raise HTTPException(410, 'client_keys_managed_by_environment')
