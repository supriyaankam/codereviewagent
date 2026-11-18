from fastapi import APIRouter, Depends, HTTPException, Query
from app.schemas import ProjectCreate, ProjectResponse
from app.services.firebase_service import create_project, get_projects, firebase_available
from app.auth import require_authenticated_user

router = APIRouter(prefix="/api/projects", tags=["projects"])

_NO_FIREBASE = HTTPException(
    status_code=503,
    detail="Firebase is not configured. Add credentials to backend/.env to enable this feature.",
)


@router.post("", response_model=ProjectResponse, status_code=201)
def create_project_route(
    body: ProjectCreate,
    authenticated_user: str = Depends(require_authenticated_user),
):
    """Create a new project for a user."""
    if body.user_id != authenticated_user:
        raise HTTPException(status_code=403, detail="You may only create projects for your own account.")
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
def get_projects_route(
    user_id: str = Query(..., description="Firebase UID"),
    authenticated_user: str = Depends(require_authenticated_user),
):
    """Return projects owned by the authenticated user."""
    if user_id != authenticated_user:
        raise HTTPException(status_code=403, detail="You may only access your own projects.")
    if not firebase_available():
        return []
    try:
        return get_projects(user_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
