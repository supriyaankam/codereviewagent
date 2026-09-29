"""
Pydantic request/response models for all API routes.
"""

from typing import Optional
from pydantic import BaseModel, Field


# ── projects ──────────────────────────────────────────────────────────────────

class ProjectCreate(BaseModel):
    user_id: str = Field(..., description="Firebase UID of the owner")
    name: str = Field(..., min_length=1, max_length=120)
    description: Optional[str] = Field(default="", max_length=500)


class ProjectResponse(BaseModel):
    id: str
    name: str
    description: str
    owner_id: str


# ── code_reviews ──────────────────────────────────────────────────────────────

class CodeReviewCreate(BaseModel):
    user_id: str = Field(..., description="Firebase UID of the author")
    project_id: str
    project_name: str = Field(..., min_length=1, max_length=120)
    language: str = Field(..., min_length=1, max_length=60)
    code: str = Field(..., min_length=1)


class CodeReviewResponse(BaseModel):
    id: str
    author_id: str
    project_id: str
    project_name: str
    language: str
    code: str
    status: str
    # Issue counts (populated when review is completed)
    overall_risk:   Optional[str] = None
    summary:        Optional[str] = None
    issues_count:   int = 0
    critical_count: int = 0
    warning_count:  int = 0
    info_count:     int = 0
    created_at:     Optional[str] = None
    updated_at:     Optional[str] = None


# ── feedback ──────────────────────────────────────────────────────────────────

class FeedbackCreate(BaseModel):
    user_id:      str = Field(..., description="Firebase UID of the reviewer")
    review_id:    str
    issue_id:     str = Field(default="",
                              description="Index or title of the AI issue being rated")
    experience_id: str = Field(default="",
                               description="Hindsight experience doc ID if available")
    decision:     str = Field(..., pattern="^(accepted|rejected|modified)$")
    note:         Optional[str] = Field(default="", max_length=1000,
                                        description="Developer reason or modified recommendation")
    # Rich fields for memory learning
    issue_title:          Optional[str] = Field(default="", max_length=300)
    issue_category:       Optional[str] = Field(default="")
    issue_severity:       Optional[str] = Field(default="")
    original_recommendation: Optional[str] = Field(default="", max_length=1000)
    modified_recommendation: Optional[str] = Field(default="", max_length=1000,
                                                    description="Developer's preferred alternative")
    project_id:   Optional[str] = Field(default="global")
    language:     Optional[str] = Field(default="")


class FeedbackResponse(BaseModel):
    id:           str
    user_id:      str
    review_id:    str
    issue_id:     str = ""
    experience_id: str = ""
    decision:     str
    note:         str = ""
    issue_title:  str = ""
    memory_stored: bool = False


# ── team_standards ────────────────────────────────────────────────────────────

STANDARD_CATEGORIES = (
    "naming", "security", "architecture", "error-handling",
    "testing", "documentation", "performance", "style", "general",
)


class TeamStandardCreate(BaseModel):
    title:       str = Field(..., min_length=1, max_length=200)
    description: str = Field(..., min_length=1, max_length=2000)
    category:    str = Field(default="general", max_length=60)
    project_id:  str = Field(default="global",
                             description="Firestore project ID or 'global'")
    created_by:  str = Field(..., description="Firebase UID of the creator")


class TeamStandardUpdate(BaseModel):
    title:       str = Field(..., min_length=1, max_length=200)
    description: str = Field(..., min_length=1, max_length=2000)
    category:    str = Field(default="general", max_length=60)


class TeamStandardResponse(BaseModel):
    id:          str
    title:       str
    description: str
    category:    str
    project_id:  str = ""
    created_by:  str = ""
    created_at:  Optional[str] = None
    updated_at:  Optional[str] = None


# ── code analysis ─────────────────────────────────────────────────────────────

class AnalyzeRequest(BaseModel):
    language: str = Field(..., min_length=1, max_length=60)
    code: str     = Field(..., min_length=1)


class AnalysisIssue(BaseModel):
    category:       str
    severity:       str
    title:          str
    description:    str
    recommendation: str
    line:           int = 0
    source:         str = "static"
    column:         int = 0


class AnalysisSummaryCounts(BaseModel):
    total:    int = 0
    critical: int = 0
    error:    int = 0
    warning:  int = 0
    info:     int = 0


class AnalysisSummary(BaseModel):
    counts:      AnalysisSummaryCounts
    by_category: dict[str, int] = {}


class AnalyzeResponse(BaseModel):
    language:  str
    issues:    list[AnalysisIssue]
    summary:   AnalysisSummary
    supported: bool
    message:   Optional[str] = None


# ── full review (static + AI) ─────────────────────────────────────────────────

class ReviewRequest(BaseModel):
    user_id:      str = Field(..., description="Firebase UID of the submitter")
    project_id:   str = Field(default="", description="Firestore project doc ID (optional)")
    project_name: str = Field(..., min_length=1, max_length=120)
    language:     str = Field(..., min_length=1, max_length=60)
    code:         str = Field(..., min_length=1)


class AIIssue(BaseModel):
    severity:       str
    category:       str
    title:          str
    explanation:    str
    recommendation: str
    line:           int = 0
    source:         str = "ai"


class ReviewResponse(BaseModel):
    review_id:      str
    language:       str
    project_name:   str
    overall_risk:   str
    summary:        str
    ai_issues:      list[AIIssue]
    static_issues:  list[AnalysisIssue]
    static_summary: AnalysisSummary
    ai_available:   bool
    fallback_reason: Optional[str] = None
    saved_to_db:    bool = False
