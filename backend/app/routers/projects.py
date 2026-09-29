from fastapi import APIRouter, HTTPException, Query
from app.schemas import ProjectCreate, ProjectResponse
from app.services.firebase_service import create_project, get_projects, firebase_available

router = APIRouter(prefix="/api/projects", tags=["projects"])

_NO_FIREBASE = HTTPException(
    status_code=503,
    detail="Firebase is not configured. Add credentials to backend/.env to enable this feature.",
)


@router.post("", response_model=ProjectResponse, status_code=201)
def create_project_route(body: ProjectCreate):
    """Create a new project for a user."""
    if not firebase_available():
        raise _NO_FIREBASE
    try:
        return create_project(
            user_id=body.user_id,
            name=body.name,
            description=body.description or "",
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("", response_model=list[ProjectResponse])
def get_projects_route(user_id: str = Query(..., description="Firebase UID")):
    """Return all projects owned by a user — empty list when Firebase not configured."""
    if not firebase_available():
        return []
    try:
        return get_projects(user_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
