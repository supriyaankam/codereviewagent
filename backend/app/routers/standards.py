from fastapi import APIRouter, HTTPException, Query
from app.schemas import TeamStandardCreate, TeamStandardUpdate, TeamStandardResponse
from app.services.firebase_service import (
    create_team_standard,
    update_team_standard,
    delete_team_standard,
    get_team_standard,
    get_all_team_standards,
    get_standards_by_project,
    firebase_available,
)

router = APIRouter(prefix="/api/standards", tags=["standards"])

_NO_FIREBASE = HTTPException(
    status_code=503,
    detail="Firebase is not configured. Add credentials to backend/.env to enable this feature.",
)


@router.get("", response_model=list[TeamStandardResponse])
def get_standards_route(
    project_id: str = Query(default="", description="Filter by project_id; omit for all"),
):
    """Return team standards — returns empty list when Firebase is not configured."""
    if not firebase_available():
        return []
    try:
        if project_id:
            return get_standards_by_project(project_id)
        return get_all_team_standards()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/{standard_id}", response_model=TeamStandardResponse)
def get_standard_route(standard_id: str):
    """Return a single team standard by ID."""
    if not firebase_available():
        raise _NO_FIREBASE
    try:
        doc = get_team_standard(standard_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    if doc is None:
        raise HTTPException(status_code=404, detail="Standard not found.")
    return doc


@router.post("", response_model=TeamStandardResponse, status_code=201)
def create_standard_route(body: TeamStandardCreate):
    """Create a new team standard."""
    if not firebase_available():
        raise _NO_FIREBASE
    try:
        return create_team_standard(
            title=body.title,
            description=body.description,
            category=body.category or "general",
            project_id=body.project_id or "global",
            created_by=body.created_by,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.put("/{standard_id}", response_model=TeamStandardResponse)
def update_standard_route(standard_id: str, body: TeamStandardUpdate):
    """Update title, description, and category of an existing standard."""
    if not firebase_available():
        raise _NO_FIREBASE
    try:
        existing = get_team_standard(standard_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="Standard not found.")
        result = update_team_standard(
            standard_id=standard_id,
            title=body.title,
            description=body.description,
            category=body.category or "general",
        )
        return {**existing, **result}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.delete("/{standard_id}", status_code=204)
def delete_standard_route(standard_id: str):
    """Hard-delete a team standard."""
    if not firebase_available():
        raise _NO_FIREBASE
    try:
        existing = get_team_standard(standard_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="Standard not found.")
        delete_team_standard(standard_id)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
