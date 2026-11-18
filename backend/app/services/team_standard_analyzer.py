import re
from typing import Iterable

from app.analyzers.language_analyzer import literal_assignments, literal_strings

SEVERITY_VALUES = {"critical", "error", "warning", "info"}
_CREDENTIAL_ASSIGNMENT = re.compile(
    r"""(?ix)
    \b(?P<name>
        api[_-]?key|api[_-]?secret|aws[_-]?access[_-]?key(?:[_-]?id)?|
        client[_-]?secret|private[_-]?key|password|passwd|pwd|
        auth(?:entication)?[_-]?token|access[_-]?token|
        token|secret(?:[_-]?key)?
    )\b
    \s*[:=]\s*
    (?:
        (?P<quote>["'])(?P<quoted>[^"'\r\n]+)["']|
        (?P<bare>[A-Z0-9_./+=-]{8,})
    )
    """
)
_PLACEHOLDER = re.compile(
    r"(?i)^(?:change(?:me)?|replace(?:_?me)?|your(?:[_ -].*)?|"
    r"example(?:[_ -].*)?|placeholder|dummy|sample|test|fake|none|null|"
    r"\*{3,}|<[^>]+>|\$\{[^}]+\})$"
)
_AWS_ACCESS_KEY = re.compile(r"(?<![A-Z0-9])AKIA[0-9A-Z]{16}(?![A-Z0-9])")
_PRIVATE_KEY_HEADER = re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")
_DATABASE_URL_ASSIGNMENT = re.compile(
    r"""(?ix)
    \b(?:db[_-]?url|database[_-]?url|connection[_-]?string|dsn)
    \s*[:=]\s*["'](?P<value>[^"'\r\n]+)["']
    """
)
_DATABASE_URL_SCHEME = re.compile(
    r"(?i)^(?:postgres(?:ql)?|mysql|mariadb|mongodb(?:\+srv)?|redis|rediss|"
    r"sqlite|oracle|mssql|sqlserver|jdbc):"
)


def _severity(standard: dict) -> str:
    value = str(standard.get("severity", "warning")).lower()
    return value if value in SEVERITY_VALUES else "warning"


def _standard_kind(title: str, description: str) -> str:
    text = f"{title} {description}".lower()
    if (
        re.search(r"\b(?:database|db)\b", text)
        and re.search(r"\b(?:url|urls|connection\s+strings?|dsn)\b", text)
    ):
        return "database_url"
    if re.search(
        r"\b(?:credentials?|api[_ -]?keys?|passwords?|passwd|pwd|"
        r"authentication\s+tokens?|auth[_ -]?tokens?|access[_ -]?tokens?|"
        r"tokens?|secrets?|private[_ -]?keys?)\b",
        text,
    ):
        return "credentials"
    if "validate" in text or "user input" in text:
        return "validation"
    if "exception" in text or "error handling" in text:
        return "exception"
    if "nested loop" in text:
        return "nested_loop"
    if "meaningful" in text or "variable name" in text:
        return "variable_name"
    if "duplicate" in text:
        return "duplicate_code"
    return "other"


def redact_credential_values(code: str) -> str:
    def replace(match: re.Match) -> str:
        value = match.group("quoted") or match.group("bare") or ""
        if (
            _PLACEHOLDER.fullmatch(value.strip())
            or (match.group("bare") and re.fullmatch(r"[A-Z][A-Z0-9_]*", value) and "_" in value)
        ):
            return match.group(0)
        return match.group(0).replace(value, "********", 1)

    redacted = _CREDENTIAL_ASSIGNMENT.sub(replace, code)
    redacted = _AWS_ACCESS_KEY.sub("********", redacted)
    return re.sub(
        r"-----BEGIN ((?:RSA |EC |OPENSSH )?PRIVATE KEY)-----.*?-----END \1-----",
        r"-----BEGIN \1-----********-----END \1-----",
        redacted,
        flags=re.DOTALL,
    )


def analyze_team_standards(
    language: str,
    code: str,
    standards: Iterable[dict],
) -> list[dict]:
    findings: list[dict] = []
    lang = language.strip().lower()
    seen_rule_locations: set[tuple[str, int, int]] = set()
    parsed_assignments = literal_assignments(lang, code) or []
    assignments_by_line: dict[int, list[tuple[str, str, int]]] = {}
    for assignment in parsed_assignments:
        assignments_by_line.setdefault(assignment[2], []).append(assignment)
    parsed_literals = literal_strings(lang, code) or []
    literals_by_line: dict[int, list[str]] = {}
    for value, line_number in parsed_literals:
        literals_by_line.setdefault(line_number, []).append(value)

    ordered_standards = sorted(
        standards,
        key=lambda standard: (
            str(standard.get("title") or standard.get("name") or "").strip().lower()
            != "never hardcode credentials"
        ),
    )
    for standard in ordered_standards:
        if not standard.get("enabled", True):
            continue

        title = str(standard.get("title") or standard.get("name") or "Team standard")
        description = str(standard.get("description") or "")
        category = str(standard.get("category") or "general")
        severity = _severity(standard)
        solution = str(
            standard.get("recommended_solution")
            or standard.get("recommendation")
            or "Apply the team rule and preserve unrelated code."
        )
        rule_kind = _standard_kind(title, description)

        for line_number, line in enumerate(code.splitlines(), 1):
            violation = False
            why = ""

            if rule_kind == "credentials":
                credential_assignment = None
                for name, value, _ in assignments_by_line.get(line_number, []):
                    credential_assignment = _CREDENTIAL_ASSIGNMENT.search(
                        f"{name} = {value}"
                    )
                    if credential_assignment:
                        break
                exposed_key = any(
                    _AWS_ACCESS_KEY.search(value) or _PRIVATE_KEY_HEADER.search(value)
                    for value in literals_by_line.get(line_number, [])
                )
                if credential_assignment or exposed_key:
                    value = (
                        credential_assignment.group("quoted")
                        or credential_assignment.group("bare")
                        or ""
                    ).strip() if credential_assignment else ""
                    if _PLACEHOLDER.fullmatch(value.strip("`'\"")):
                        continue
                    violation = True
                    why = "This line assigns a literal credential value in source code."
                    solution = "Move the credential to an environment variable or secrets manager; rotate it if it was committed."

            elif rule_kind == "database_url":
                database_url = next(
                    (
                        match
                        for name, value, _ in assignments_by_line.get(line_number, [])
                        if (match := _DATABASE_URL_ASSIGNMENT.search(f"{name} = {value}"))
                    ),
                    None,
                )
                if database_url and _DATABASE_URL_SCHEME.match(database_url.group("value")):
                    violation = True
                    why = "This line assigns a hardcoded database connection URL."
                    solution = "Load the database URL from an environment variable or secure configuration service."

            elif rule_kind == "validation":
                risky_input = re.search(r"(?i)\b(input)\s*\(", line)
                eval_input = re.search(r"(?i)\b(eval|exec)\s*\(", line)
                if risky_input or eval_input:
                    violation = True
                    why = "User input is used without an explicit validation or type-checking step near this line."

            elif rule_kind == "exception":
                if re.search(r"(?i)^\s*except\s*:", line) or re.search(r"(?i)^\s*(except\s+\w+.*:\s*pass)\s*$", line):
                    violation = True
                    why = "The exception handler is too broad or silently ignores the error."

            elif rule_kind == "nested_loop":
                if lang == "python":
                    stripped = line.lstrip()
                    if stripped.startswith("for ") and len(line) - len(stripped) > 4:
                        violation = True
                        why = "This loop is nested; the rule requires avoiding unnecessary nested iteration."

            elif rule_kind == "variable_name":
                if lang == "python":
                    match = re.search(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*=", line)
                    if match and len(match.group(1)) < 3 and match.group(1) not in {"id", "db", "os"}:
                        violation = True
                        why = f"The variable name '{match.group(1)}' is too short to be meaningful."

            elif rule_kind == "duplicate_code":
                line_text = line.strip()
                if len(line_text) >= 24:
                    if sum(1 for candidate in code.splitlines() if candidate.strip() == line_text) > 1:
                        violation = True
                        why = "The same code appears more than once, indicating duplicated logic."

            if not violation:
                continue

            location = (rule_kind, line_number, max(0, len(line) - len(line.lstrip()) + 1))
            if location in seen_rule_locations:
                continue
            seen_rule_locations.add(location)

            finding = {
                "category": category,
                "severity": severity,
                "title": f"[Team Standard] {title}",
                "description": why,
                "recommendation": solution,
                "line": line_number,
                "column": max(0, len(line) - len(line.lstrip()) + 1),
                "source": "team_standard",
                "team_standard": True,
                "standard_id": str(standard.get("id") or ""),
                "standard_name": title,
            }
            findings.append(finding)

    return findings
