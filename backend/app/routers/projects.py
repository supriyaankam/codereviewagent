from fastapi import APIRouter, Depends, HTTPException, Query
from app.schemas import ProjectCreate, ProjectResponse
from app.services.firebase_service import create_project, get_projects, firebase_available
from app.services import project_cloud_service, project_upload_service
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


@router.get("/history")
def list_project_history_route(
    page_size: int = Query(default=20, ge=1, le=50),
    cursor: str | None = Query(default=None, min_length=1, max_length=128),
    authenticated_user: str = Depends(require_authenticated_user),
):
    """Return one page of uploaded projects belonging to the caller."""
    if not firebase_available():
        raise _NO_FIREBASE
    try:
        return project_cloud_service.list_project_history(
            authenticated_user,
            page_size,
            cursor,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/history/{project_id}/analyses")
def list_project_analysis_history_route(
    project_id: str,
    page_size: int = Query(default=20, ge=1, le=50),
    cursor: str | None = Query(default=None, min_length=1, max_length=128),
    authenticated_user: str = Depends(require_authenticated_user),
):
    """Return one page of saved analysis runs for an owned upload."""
    if not firebase_available():
        raise _NO_FIREBASE
    try:
        history = project_cloud_service.list_analysis_history(
            project_id,
            authenticated_user,
            page_size,
            cursor,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if history is None:
        raise HTTPException(status_code=404, detail="Project history not found.")
    return history


@router.get("/history/{project_id}/analyses/{analysis_id}")
def get_project_analysis_history_route(
    project_id: str,
    analysis_id: str,
    authenticated_user: str = Depends(require_authenticated_user),
):
    """Return a saved analysis run and its findings for an owned upload."""
    if not firebase_available():
        raise _NO_FIREBASE
    analysis = project_cloud_service.get_analysis_history(
        project_id,
        analysis_id,
        authenticated_user,
    )
    if analysis is None:
        raise HTTPException(status_code=404, detail="Project analysis not found.")
    source_storage = project_cloud_service.project_source_storage(project_id, authenticated_user)
    source_available = source_storage == "cloud" or project_upload_service.source_files_available(
        project_id,
        authenticated_user,
    )
    analysis["source_available"] = source_available
    analysis["source_storage"] = source_storage if source_available else "expired"
    return analysis
