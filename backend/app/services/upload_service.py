"""
Upload service — safely read source files from uploads.

Supports:
  - Single .py file upload
  - ZIP archive containing a project folder

SAFETY:
  - Uploaded code is NEVER executed, imported, or eval'd.
  - File paths inside ZIPs are sanitised to prevent directory traversal.
  - ZIP entries are validated and extracted source size is capped.
  - Only recognised source-code extensions are extracted.
"""

from __future__ import annotations

import io
import re
import zipfile
from typing import BinaryIO
from pathlib import PurePosixPath

# ── Limits ────────────────────────────────────────────────────────────────────

MAX_TOTAL_BYTES  = 500 * 1024 * 1024  # 500 MiB total extracted from ZIP
MAX_FILE_BYTES   = 50  * 1024 * 1024  # 50 MiB per single file upload
MAX_ZIP_BYTES    = 500 * 1024 * 1024  # 500 MiB raw ZIP upload size
MAX_FILE_IN_ZIP  = 20  * 1024 * 1024  # 20 MiB per source file inside ZIP
MAX_FILES        = 1000               # max number of source files per ZIP
_MAX_REPORTED_SKIPPED_FILES = 1000

# ── Supported extensions → language tag ──────────────────────────────────────

EXTENSION_MAP: dict[str, str] = {
    ".py":   "python",
    ".pyi":  "python",
    ".js":   "javascript",
    ".mjs":  "javascript",
    ".cjs":  "javascript",
    ".ts":   "typescript",
    ".mts":  "typescript",
    ".cts":  "typescript",
    ".jsx":  "javascript",
    ".tsx":  "typescript",
    ".java": "java",
    ".cs":   "csharp",
    ".csx":  "csharp",
    ".go":   "go",
    ".rb":   "ruby",
    ".rbw":  "ruby",
    ".php":  "php",
    ".cpp":  "cpp",
    ".cc":   "cpp",
    ".cxx":  "cpp",
    ".c++":  "cpp",
    ".hpp":  "cpp",
    ".hh":   "cpp",
    ".hxx":  "cpp",
    ".c":    "c",
    ".rs":   "rust",
    ".kt":   "kotlin",
    ".kts":  "kotlin",
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


def zip_source_entries(
    zf: zipfile.ZipFile,
    skipped_files: list[dict[str, str]] | None = None,
    additional_skip_dirs: set[str] | None = None,
) -> list[tuple[zipfile.ZipInfo, str, str]]:
    """Validate a ZIP central directory and return supported source entries without reading them."""
    entries: list[tuple[zipfile.ZipInfo, str, str]] = []
    total_bytes = 0
    skipped_large = 0

    def report_skipped(path: str, reason: str) -> None:
        if skipped_files is None:
            return
        if len(skipped_files) < _MAX_REPORTED_SKIPPED_FILES:
            skipped_files.append({"path": path, "reason": reason})
        elif len(skipped_files) == _MAX_REPORTED_SKIPPED_FILES:
            skipped_files.append({
                "path": "(additional ZIP entries)",
                "reason": (
                    f"Further skipped entries are not listed; the report is limited to "
                    f"{_MAX_REPORTED_SKIPPED_FILES} paths."
                ),
            })

    for info in zf.infolist():
        path = PurePosixPath(info.filename)
        if (
            not path.parts
            or path.is_absolute()
            or "\\" in info.filename
            or re.match(r"^[A-Za-z]:", info.filename)
            or any(part in ("", ".", "..") for part in path.parts)
            or any(
                part.endswith((" ", "."))
                or any(char in '<>:"|?*' for char in part)
                or part.upper().split(".")[0] in {
                    "CON", "PRN", "AUX", "NUL",
                    *(f"COM{index}" for index in range(1, 10)),
                    *(f"LPT{index}" for index in range(1, 10)),
                }
                for part in path.parts
            )
        ):
            raise ValueError(f"ZIP archive contains an unsafe path: {info.filename!r}.")

        if info.filename.endswith("/"):
            continue
        if _should_skip(path, additional_skip_dirs):
            report_skipped(
                info.filename,
                "Generated, dependency, or hidden paths are skipped.",
            )
            continue

        language = EXTENSION_MAP.get(path.suffix.lower())
        if language is None:
            report_skipped(info.filename, "Unsupported source file type.")
            continue
        if info.file_size > MAX_FILE_IN_ZIP:
            skipped_large += 1
            report_skipped(
                info.filename,
                f"File exceeds the {MAX_FILE_IN_ZIP // (1024*1024)} MB ZIP source-file limit.",
            )
            continue

        if len(entries) >= MAX_FILES:
            report_skipped(
                info.filename,
                f"ZIP source-file limit ({MAX_FILES}) was reached.",
            )
            continue
        total_bytes += info.file_size
        if total_bytes > MAX_TOTAL_BYTES:
            raise ValueError(
                f"ZIP archive total extracted size exceeds "
                f"{MAX_TOTAL_BYTES // (1024*1024)} MB limit."
            )
        entries.append((info, info.filename, language))

    if not entries:
        detail = (
            "No supported source files found in the ZIP. "
            f"Supported extensions: {', '.join(EXTENSION_MAP)}"
            + (
                f" ({skipped_large} file(s) skipped — exceeded "
                f"{MAX_FILE_IN_ZIP // (1024*1024)} MB per-file limit)."
                if skipped_large else "."
            )
        )
        if skipped_files:
            listed = skipped_files[:10]
            detail += " Excluded entries: " + "; ".join(
                f"{item['path']}: {item['reason']}" for item in listed
            )
            if len(skipped_files) > len(listed):
                detail += f"; and {len(skipped_files) - len(listed)} more."
        raise ValueError(detail)
    return entries


def read_zip_archive(
    data: bytes | BinaryIO,
    skipped_files: list[dict[str, str]] | None = None,
    additional_skip_dirs: set[str] | None = None,
) -> list[SourceFile]:
    """
    Extract all recognised source files from a ZIP archive.
    Returns a list of SourceFile objects.
    Raises ValueError for invalid ZIPs or size violations.
    """
    archive_file: BinaryIO
    if isinstance(data, bytes):
        archive_file = io.BytesIO(data)
        archive_size = len(data)
    else:
        archive_file = data
        try:
            archive_file.seek(0, 2)
            archive_size = archive_file.tell()
            archive_file.seek(0)
        except (OSError, AttributeError) as exc:
            raise ValueError("ZIP upload must support seeking.") from exc

    if not zipfile.is_zipfile(archive_file):
        raise ValueError("Uploaded file is not a valid ZIP archive.")

    if archive_size > MAX_ZIP_BYTES:
        raise ValueError(
            f"ZIP file is {archive_size // (1024*1024)} MB — "
            f"maximum allowed is {MAX_ZIP_BYTES // (1024*1024)} MB."
        )

    files: list[SourceFile] = []
    archive_file.seek(0)
    with zipfile.ZipFile(archive_file) as zf:
        for info, path, language in zip_source_entries(
            zf,
            skipped_files,
            additional_skip_dirs,
        ):
            raw = zf.read(info.filename)
            content = _decode(raw, info.filename)
            files.append(SourceFile(
                path=path,
                language=language,
                content=content,
            ))
    return files


# ── Helpers ───────────────────────────────────────────────────────────────────

def should_skip_path(path: PurePosixPath, additional_dirs: set[str] | None = None) -> bool:
    """Return True if any path part is hidden or in the configured skip list."""
    parts = {part.casefold() for part in path.parts[:-1]}
    skip_dirs = {part.casefold() for part in _SKIP_DIRS | (additional_dirs or set())}
    if parts & skip_dirs:
        return True
    # Skip hidden files/dirs (starting with .)
    for part in path.parts:
        if part.startswith("."):
            return True
    return False


def _should_skip(path: PurePosixPath, additional_dirs: set[str] | None = None) -> bool:
    return should_skip_path(path, additional_dirs)


def _decode(data: bytes, filename: str) -> str:
    """Decode bytes to str, trying UTF-8 then latin-1."""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        try:
            return data.decode("latin-1")
        except UnicodeDecodeError:
            raise ValueError(f"File '{filename}' could not be decoded as text.")
