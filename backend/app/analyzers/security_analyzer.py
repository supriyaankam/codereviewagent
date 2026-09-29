"""
Security analyzer — Bandit pass + regex-based hardcoded-secret detection.

SAFETY: submitted code is NEVER executed.
  - Bandit runs via its Python API against a tempfile; the file is never imported.
  - Regex patterns only inspect the raw source string.
"""

import os
import re
import tempfile
from dataclasses import dataclass

import bandit
from bandit.core import manager as bandit_manager
from bandit.core import config as bandit_config

from app.analyzers.base import Issue, Severity, Category


# ── Bandit severity / confidence → our Severity ──────────────────────────────

_BANDIT_SEV: dict[str, Severity] = {
    "HIGH":   Severity.critical,
    "MEDIUM": Severity.warning,
    "LOW":    Severity.info,
}


# ── Hardcoded secret patterns ─────────────────────────────────────────────────

@dataclass
class _SecretPattern:
    name: str
    pattern: re.Pattern
    recommendation: str


_SECRET_PATTERNS: list[_SecretPattern] = [
    _SecretPattern(
        name="Hardcoded password assignment",
        pattern=re.compile(
            r'(?i)(password|passwd|pwd)\s*=\s*["\'][^"\']{4,}["\']',
        ),
        recommendation="Store passwords in environment variables or a secrets manager. Never hardcode them.",
    ),
    _SecretPattern(
        name="Hardcoded API key",
        pattern=re.compile(
            r'(?i)(api_key|apikey|api_secret)\s*=\s*["\'][A-Za-z0-9_\-]{8,}["\']',
        ),
        recommendation="Move API keys to environment variables (e.g. os.environ['API_KEY']) or a vault.",
    ),
    _SecretPattern(
        name="Hardcoded secret / token",
        pattern=re.compile(
            r'(?i)(secret|token|auth_token|access_token)\s*=\s*["\'][A-Za-z0-9_\-\.]{8,}["\']',
        ),
        recommendation="Move secrets/tokens to environment variables or a secrets manager.",
    ),
    _SecretPattern(
        name="Hardcoded AWS access key ID",
        pattern=re.compile(r'(?<![A-Z0-9])(AKIA[0-9A-Z]{16})(?![A-Z0-9])'),
        recommendation="Rotate this AWS key immediately and store credentials via IAM roles or AWS Secrets Manager.",
    ),
    _SecretPattern(
        name="Hardcoded private key block",
        pattern=re.compile(r'-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----'),
        recommendation="Never embed private keys in source code. Use secure key stores or environment injection.",
    ),
    _SecretPattern(
        name="Hardcoded database connection string",
        pattern=re.compile(
            r'(?i)(db_url|database_url|connection_string|dsn)\s*=\s*["\'][^"\']{10,}["\']',
        ),
        recommendation="Store database URLs in environment variables or a configuration service.",
    ),
]


# ── Bandit analysis ───────────────────────────────────────────────────────────

def run_bandit_analysis(code: str) -> list[Issue]:
    """
    Run Bandit against a tempfile (never imported or executed).
    Returns structured Issues from Bandit findings.
    """
    issues: list[Issue] = []

    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".py",
        prefix="cra_bandit_",
        delete=False,
        encoding="utf-8",
    ) as tmp:
        tmp.write(code)
        tmp_path = tmp.name

    try:
        conf = bandit_config.BanditConfig()
        mgr = bandit_manager.BanditManager(conf, agg_type="file", debug=False)
        mgr.discover_files([tmp_path], recursive=False)
        mgr.run_tests()

        for issue in mgr.get_issue_list():
            severity = _BANDIT_SEV.get(issue.severity.upper(), Severity.info)
            issues.append(Issue(
                category=Category.security,
                severity=severity,
                title=f"[{issue.test_id}] {issue.test}",
                description=issue.text,
                recommendation=_bandit_recommendation(issue.test_id),
                line=issue.lineno,
                source="bandit",
                column=issue.col_offset if hasattr(issue, "col_offset") else 0,
            ))
    except Exception:  # noqa: BLE001 — never let analysis crash the endpoint
        pass
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    return issues


def _bandit_recommendation(test_id: str) -> str:
    recs: dict[str, str] = {
        "B101": "Replace assert with explicit if/raise for production runtime validation.",
        "B102": "Avoid exec() — refactor to explicit function calls.",
        "B103": "Avoid setting file permissions to overly broad values.",
        "B104": "Binding to 0.0.0.0 exposes the service on all interfaces. Use a specific address in production.",
        "B105": "Replace hardcoded password string with an environment variable or secret manager.",
        "B106": "Replace hardcoded password argument with an environment variable.",
        "B107": "Replace hardcoded password default with None and require callers to supply it.",
        "B108": "Use a secure, application-specific temp directory (tempfile.mkdtemp()).",
        "B110": "Log the exception before passing; silent except/pass hides errors.",
        "B112": "Log the exception before continuing; silent loops hide errors.",
        "B201": "Flask debug mode must be disabled in production (DEBUG=False).",
        "B202": "Avoid Flask debug mode in production.",
        "B301": "Use json.loads() instead of pickle — pickle can execute arbitrary code.",
        "B302": "Use json.loads() instead of marshal — marshal can execute arbitrary code.",
        "B303": "MD5 is cryptographically broken. Use SHA-256 or stronger.",
        "B304": "RC2/RC4 ciphers are insecure. Use AES-256-GCM.",
        "B305": "ECB mode is insecure. Use AES-GCM or AES-CBC with a random IV.",
        "B306": "mktemp() has a race condition. Use tempfile.mkstemp() instead.",
        "B307": "eval() is a security risk. Use ast.literal_eval() for safe literal parsing.",
        "B308": "mark_safe() bypasses Django's XSS protection. Avoid it with user-controlled data.",
        "B310": "Audit url schemes carefully to prevent SSRF.",
        "B311": "random is not cryptographically secure. Use secrets module for security-sensitive values.",
        "B312": "telnetlib is insecure. Use SSH (paramiko) instead.",
        "B313": "xml.etree is vulnerable to XML bombs. Use defusedxml.",
        "B314": "xml.etree is vulnerable to XML bombs. Use defusedxml.",
        "B315": "xml.etree is vulnerable to XML bombs. Use defusedxml.",
        "B316": "xml.etree is vulnerable to XML bombs. Use defusedxml.",
        "B317": "xml.etree is vulnerable to XML bombs. Use defusedxml.",
        "B318": "xml.etree is vulnerable to XML bombs. Use defusedxml.",
        "B319": "xml.etree is vulnerable to XML bombs. Use defusedxml.",
        "B320": "xml.etree is vulnerable to XML bombs. Use defusedxml.",
        "B321": "FTP is unencrypted. Use SFTP or FTPS.",
        "B322": "input() in Python 2 evaluates expressions. In Python 3 it is safe.",
        "B323": "Unverified SSL context disables certificate validation. Pass verify=True.",
        "B324": "MD5/SHA1 are weak hashes. Use SHA-256 or stronger.",
        "B325": "mktemp() has a race condition. Use tempfile.mkstemp() instead.",
        "B401": "telnetlib is insecure. Use SSH.",
        "B402": "ftplib is unencrypted. Use SFTP.",
        "B403": "pickle can execute arbitrary code when loading untrusted data.",
        "B404": "subprocess use requires careful argument sanitisation to prevent injection.",
        "B405": "xml.etree is vulnerable to entity expansion. Use defusedxml.",
        "B406": "xml.etree is vulnerable to entity expansion. Use defusedxml.",
        "B407": "xml.etree is vulnerable to entity expansion. Use defusedxml.",
        "B408": "xml.minidom is vulnerable. Use defusedxml.",
        "B409": "xml.pulldom is vulnerable. Use defusedxml.",
        "B410": "lxml is vulnerable to certain XML attacks. Keep it updated.",
        "B411": "xmlrpc is vulnerable to entity expansion. Consider alternatives.",
        "B412": "httpoxy vulnerability. Pin WSGI implementations.",
        "B413": "pycrypto is deprecated. Use pycryptodome.",
        "B501": "TLS/SSL certificate verification is disabled. Enable it.",
        "B502": "Avoid ssl.PROTOCOL_SSLv2/v3 — use TLS 1.2+.",
        "B503": "Avoid ssl.PROTOCOL_SSLv2/v3.",
        "B504": "ssl.wrap_socket without ssl_version specified defaults to insecure SSLv2.",
        "B505": "Weak cryptographic key size. Use RSA ≥ 2048, EC ≥ 256.",
        "B506": "yaml.load() with an untrusted loader can execute arbitrary code. Use yaml.safe_load().",
        "B507": "Paramiko without host-key verification is vulnerable to MITM. Set reject_policy.",
        "B601": "Avoid paramiko exec_command with shell=True.",
        "B602": "subprocess with shell=True and user-controlled input enables command injection.",
        "B603": "subprocess without shell=True is safer; ensure args are not user-controlled.",
        "B604": "Function calls with shell=True are dangerous.",
        "B605": "os.system() with user input enables command injection. Use subprocess with a list.",
        "B606": "os.system() without shell should still sanitise arguments.",
        "B607": "Start process with partial path; use full path to avoid PATH hijacking.",
        "B608": "Possible SQL injection via string formatting. Use parameterised queries.",
        "B609": "Wildcard injection in Linux commands. Avoid wildcards with user input.",
        "B610": "Django extra() with user input enables SQL injection. Use ORM methods.",
        "B611": "Django RawSQL with user input enables SQL injection. Use parameterised queries.",
        "B701": "Jinja2 autoescape disabled. Enable it to prevent XSS.",
        "B702": "Mako autoescape disabled. Enable it to prevent XSS.",
        "B703": "Django mark_safe with user input enables XSS.",
    }
    return recs.get(test_id, f"Review the Bandit documentation for test {test_id}.")


# ── Regex secret detection ────────────────────────────────────────────────────

def run_secret_detection(code: str) -> list[Issue]:
    """
    Scan raw source text for hardcoded secrets using regex patterns.
    Never executes code; operates entirely on strings.
    """
    issues: list[Issue] = []
    lines = code.splitlines()

    for lineno, line in enumerate(lines, start=1):
        # Skip obvious test / example / placeholder lines
        if re.search(r'(?i)(example|placeholder|your[_\-]?key|changeme|<.*?>)', line):
            continue
        for sp in _SECRET_PATTERNS:
            if sp.pattern.search(line):
                issues.append(Issue(
                    category=Category.security,
                    severity=Severity.critical,
                    title=sp.name,
                    description=f"Possible hardcoded secret detected on line {lineno}.",
                    recommendation=sp.recommendation,
                    line=lineno,
                    source="secrets",
                ))
                break  # one issue per line is enough

    return issues
