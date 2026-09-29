from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services.code_analyzer import analyze_code

router = APIRouter(prefix="/api", tags=["analyze"])


class AnalyzeRequest(BaseModel):
    language: str = Field(..., min_length=1, max_length=60,
                          description="Programming language of the submitted code")
    code: str = Field(..., min_length=1,
                      description="Source code to analyze (never executed)")


@router.post("/analyze")
def analyze_route(body: AnalyzeRequest):
    """
    Analyze submitted source code without executing it.

    Currently supports: **Python**

    Returns structured issues with category, severity, title,
    description, recommendation, and line number.
    """
    try:
        result = analyze_code(language=body.language, code=body.code)
        return result
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
