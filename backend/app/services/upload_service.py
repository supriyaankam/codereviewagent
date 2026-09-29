"""
Upload service — safely read source files from uploads.

Supports:
  - Single .py file upload
  - ZIP archive containing a project folder

SAFETY:
  - Uploaded code is NEVER executed, imported, or eval'd.
  - File paths inside ZIPs are sanitised to prevent directory traversal.
  - Maximum total extracted size is capped at 5 MB.
  - Only recognised source-code extensions are extracted.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import PurePosixPath

# ── Limits ────────────────────────────────────────────────────────────────────

MAX_TOTAL_BYTES  = 100 * 1024 * 1024  # 100 MB total extracted from ZIP
MAX_FILE_BYTES   = 10  * 1024 * 1024  # 10 MB per single file upload
MAX_ZIP_BYTES    = 100 * 1024 * 1024  # 100 MB raw ZIP upload size
MAX_FILE_IN_ZIP  = 5   * 1024 * 1024  # 5 MB per individual file inside ZIP (skip larger ones)
MAX_FILES        = 1000               # max number of source files per ZIP

# ── Supported extensions → language tag ──────────────────────────────────────

EXTENSION_MAP: dict[str, str] = {
    ".py":   "python",
    ".js":   "javascript",
    ".ts":   "typescript",
    ".jsx":  "javascript",
    ".tsx":  "typescript",
    ".java": "java",
    ".cs":   "csharp",
    ".go":   "go",
    ".rb":   "ruby",
    ".php":  "php",
    ".cpp":  "cpp",
    ".c":    "c",
    ".rs":   "rust",
    ".kt":   "kotlin",
    ".swift":"swift",
}

# Directories to always skip inside a ZIP
_SKIP_DIRS = {
    "node_modules", ".git", "__pycache__", ".venv", "venv",
    "dist", "build", ".next", ".nuxt", "coverage", ".mypy_cache",
}


# ── Data class ────────────────────────────────────────────────────────────────

class SourceFile:
    """A single source file extracted from an upload."""
    __slots__ = ("path", "language", "content")

    def __init__(self, path: str, language: str, content: str) -> None:
        self.path     = path       # relative path string
        self.language = language
        self.content  = content


# ── Public API ────────────────────────────────────────────────────────────────

def read_single_file(filename: str, data: bytes) -> SourceFile:
    """
    Parse a single uploaded source file.
    Raises ValueError for unsupported types or oversized content.
    """
    if len(data) > MAX_FILE_BYTES:
        raise ValueError(
            f"File '{filename}' is {len(data) // (1024*1024):.1f} MB — "
            f"maximum allowed is {MAX_FILE_BYTES // (1024*1024)} MB."
        )

    ext = PurePosixPath(filename).suffix.lower()
    language = EXTENSION_MAP.get(ext)
    if language is None:
        raise ValueError(
            f"Unsupported file type '{ext}'. "
            f"Supported: {', '.join(EXTENSION_MAP)}"
        )

    content = _decode(data, filename)
    return SourceFile(path=filename, language=language, content=content)


def read_zip_archive(data: bytes) -> list[SourceFile]:
    """
    Extract all recognised source files from a ZIP archive.
    Returns a list of SourceFile objects.
    Raises ValueError for invalid ZIPs or size violations.
    """
    if not zipfile.is_zipfile(io.BytesIO(data)):
        raise ValueError("Uploaded file is not a valid ZIP archive.")

    # Validate raw ZIP size before extracting
    if len(data) > MAX_ZIP_BYTES:
        raise ValueError(
            f"ZIP file is {len(data) // (1024*1024)} MB — "
            f"maximum allowed is {MAX_ZIP_BYTES // (1024*1024)} MB."
        )

    files: list[SourceFile] = []
    total_bytes = 0
    skipped_large = 0

    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        for info in zf.infolist():
            # Skip directories
            if info.filename.endswith("/"):
                continue

            path = PurePosixPath(info.filename)

            # Skip hidden, skipped dirs, and unsupported extensions
            if _should_skip(path):
                continue

            ext = path.suffix.lower()
            language = EXTENSION_MAP.get(ext)
            if language is None:
                continue

            # Skip individual files that are too large (e.g. generated/minified)
            if info.file_size > MAX_FILE_IN_ZIP:
                skipped_large += 1
                continue

            total_bytes += info.file_size
            if total_bytes > MAX_TOTAL_BYTES:
                raise ValueError(
                    f"ZIP archive total extracted size exceeds "
                    f"{MAX_TOTAL_BYTES // (1024*1024)} MB limit."
                )

            if len(files) >= MAX_FILES:
                break

            raw = zf.read(info.filename)
            content = _decode(raw, info.filename)
            files.append(SourceFile(
                path=info.filename,
                language=language,
                content=content,
            ))

    if not files:
        raise ValueError(
            "No supported source files found in the ZIP. "
            f"Supported extensions: {', '.join(EXTENSION_MAP)}"
            + (f" ({skipped_large} file(s) skipped — exceeded {MAX_FILE_IN_ZIP // (1024*1024)} MB per-file limit)." if skipped_large else ".")
        )

    return files


# ── Helpers ───────────────────────────────────────────────────────────────────

def _should_skip(path: PurePosixPath) -> bool:
    """Return True if any part of the path is in the skip list."""
    parts = set(path.parts[:-1])   # exclude filename itself
    if parts & _SKIP_DIRS:
        return True
    # Skip hidden files/dirs (starting with .)
    for part in path.parts:
        if part.startswith("."):
            return True
    return False


def _decode(data: bytes, filename: str) -> str:
    """Decode bytes to str, trying UTF-8 then latin-1."""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        try:
            return data.decode("latin-1")
        except UnicodeDecodeError:
            raise ValueError(f"File '{filename}' could not be decoded as text.")
