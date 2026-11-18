"""Authentication dependencies for Firebase-protected API routes."""

from fastapi import Depends, HTTPException
from firebase_admin import auth as firebase_auth
from firebase_admin.exceptions import FirebaseError
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

_bearer_scheme = HTTPBearer(auto_error=False)


async def require_authenticated_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> str:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=401, detail="Sign in to access this resource.")
    try:
        decoded_token = firebase_auth.verify_id_token(credentials.credentials)
    except (FirebaseError, ValueError):
        raise HTTPException(status_code=401, detail="Your session is invalid or expired. Sign in again.")
    user_id = decoded_token.get("uid")
    if not isinstance(user_id, str) or not user_id:
        raise HTTPException(status_code=401, detail="Your session is invalid. Sign in again.")
    return user_id
