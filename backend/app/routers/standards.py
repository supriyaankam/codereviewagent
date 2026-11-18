from fastapi import APIRouter, Depends, HTTPException, Query
from app.auth import require_authenticated_user
from app.schemas import TeamStandardCreate, TeamStandardUpdate, TeamStandardResponse
from app.services.firebase_service import (
    create_team_standard,
    update_team_standard,
    delete_team_standard,
    get_team_standard,
    ensure_default_team_standards,
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
    authenticated_user: str = Depends(require_authenticated_user),
):
    """Return team standards — returns empty list when Firebase is not configured."""
    if not firebase_available():
        return []
    try:
        standards = ensure_default_team_standards(authenticated_user)
        if project_id:
            project_standards = [
                row for row in standards
                if row.get("project_id") in ("global", project_id)
            ]
            return project_standards
        return standards
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/{standard_id}", response_model=TeamStandardResponse)
def get_standard_route(
    standard_id: str,
    authenticated_user: str = Depends(require_authenticated_user),
):
    """Return a single team standard by ID."""
    if not firebase_available():
        raise _NO_FIREBASE
    try:
        doc = get_team_standard(standard_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    if doc is None:
        raise HTTPException(status_code=404, detail="Standard not found.")
    if doc.get("created_by") != authenticated_user:
        raise HTTPException(status_code=404, detail="Standard not found.")
    return doc


@router.post("", response_model=TeamStandardResponse, status_code=201)
def create_standard_route(
    body: TeamStandardCreate,
    authenticated_user: str = Depends(require_authenticated_user),
):
    """Create a new team standard."""
    if body.created_by != authenticated_user:
        raise HTTPException(status_code=403, detail="You may only create standards for your own account.")
    if not firebase_available():
        raise _NO_FIREBASE
    try:
        return create_team_standard(
            title=body.name or body.title,
            description=body.description,
            category=body.category or "general",
            project_id=body.project_id or "global",
            created_by=body.created_by,
            severity=body.severity,
            recommended_solution=body.recommended_solution,
            enabled=body.enabled,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.put("/{standard_id}", response_model=TeamStandardResponse)
def update_standard_route(
    standard_id: str,
    body: TeamStandardUpdate,
    authenticated_user: str = Depends(require_authenticated_user),
):
    """Update title, description, and category of an existing standard."""
    if not firebase_available():
        raise _NO_FIREBASE
    try:
        existing = get_team_standard(standard_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="Standard not found.")
        if existing.get("created_by") != authenticated_user:
            raise HTTPException(status_code=404, detail="Standard not found.")
        result = update_team_standard(
            standard_id=standard_id,
            title=body.title,
            description=body.description,
            category=body.category or "general",
            severity=body.severity,
            recommended_solution=body.recommended_solution,
            enabled=body.enabled,
        )
        return {**existing, **result}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.delete("/{standard_id}", status_code=204)
def delete_standard_route(
    standard_id: str,
    authenticated_user: str = Depends(require_authenticated_user),
):
    """Hard-delete a team standard."""
    if not firebase_available():
        raise _NO_FIREBASE
    try:
        existing = get_team_standard(standard_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="Standard not found.")
        if existing.get("created_by") != authenticated_user:
            raise HTTPException(status_code=404, detail="Standard not found.")
        delete_team_standard(standard_id)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
