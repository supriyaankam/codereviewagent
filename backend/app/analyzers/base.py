"""
Shared types used by all analyzers.
"""

from dataclasses import dataclass, field
from enum import Enum


class Severity(str, Enum):
    critical = "critical"
    error    = "error"
    warning  = "warning"
    info     = "info"


class Category(str, Enum):
    syntax     = "syntax"
    security   = "security"
    quality    = "quality"
    naming     = "naming"
    unused     = "unused"
    complexity = "complexity"


@dataclass
class Issue:
    category:       Category
    severity:       Severity
    title:          str
    description:    str
    recommendation: str
    line:           int = 0
    source:         str = "static"   # "ast" | "pylint" | "bandit" | "secrets" | "ai" | "syntax"
    column:         int = 0
    team_standard:  bool = False
    standard_id:    str = ""
    standard_name:  str = ""

    def to_dict(self) -> dict:
        return {
            "category":       self.category.value,
            "severity":       self.severity.value,
            "title":          self.title,
            "description":    self.description,
            "recommendation": self.recommendation,
            "line":           self.line,
            "source":         self.source,
            "column":         self.column,
            "team_standard":  self.team_standard,
            "standard_id":    self.standard_id,
            "standard_name":  self.standard_name,
        }
