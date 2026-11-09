"""Legacy Management project CRUD is retired; API Gateway owns canonical projects."""

from fastapi import APIRouter, HTTPException

router = APIRouter()


@router.api_route("", methods=["GET", "POST", "PATCH", "DELETE"])
@router.api_route("/", methods=["GET", "POST", "PATCH", "DELETE"])
@router.api_route("/{project_id}", methods=["GET", "POST", "PATCH", "DELETE"])
def legacy_project_retired(project_id: str | None = None) -> None:
    raise HTTPException(410, "canonical_projects_are_managed_by_api_gateway")
