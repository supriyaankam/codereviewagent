from dotenv import load_dotenv
load_dotenv()  # load .env before any other import that reads env vars

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

from app.auth import require_authenticated_user
from app.routers import projects, reviews, feedback, standards, analyze, review, fix, dashboard
from app.services.ai_reviewer import test_groq_connection
from app.services.firebase_service import firebase_health_check
from app.services.project_upload_service import cleanup_expired_uploads

logger = logging.getLogger(__name__)


async def _cleanup_project_uploads() -> None:
    while True:
        try:
            await asyncio.to_thread(cleanup_expired_uploads)
        except (OSError, RuntimeError):
            logger.exception("Could not clean up expired project uploads.")
        await asyncio.sleep(60 * 60)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    cleanup_task = asyncio.create_task(_cleanup_project_uploads())
    try:
        yield
    finally:
        cleanup_task.cancel()
        try:
            await cleanup_task
        except asyncio.CancelledError:
            pass


app = FastAPI(
    title="Code Review Agent API",
    description="AI-powered code review with team memory",
    version="0.1.0",
    lifespan=lifespan,
)

# Allow requests from the React dev server (ports 3000 and 3001)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://localhost:3001",
        "http://localhost:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routers ──────────────────────────────────────────────────────────────────
app.include_router(projects.router)
app.include_router(reviews.router)
app.include_router(feedback.router)
app.include_router(standards.router)
app.include_router(analyze.router)
app.include_router(review.router)
app.include_router(fix.router)
app.include_router(dashboard.router)


# ── Health ───────────────────────────────────────────────────────────────────
@app.get("/api/health", tags=["health"])
def health_check():
    return {
        "status": "ok",
        "message": "Code Review Agent backend is running",
    }


# ── Groq connection test ──────────────────────────────────────────────────────
@app.get("/api/groq-test", tags=["health"])
def groq_test(_: str = Depends(require_authenticated_user)):
    """Verify the Groq API key and connection. Safe to call — never returns the key."""
    return test_groq_connection()


# ── Firebase / Firestore health check ────────────────────────────────────────
@app.get("/api/firebase/health", tags=["health"])
def firebase_health(_: str = Depends(require_authenticated_user)):
    """
    Verify Firebase Admin SDK and Firestore connection.
    Writes + reads + deletes a test document in 'firebase_connection_test'.
    Never exposes service-account credentials.
    """
    return firebase_health_check()
