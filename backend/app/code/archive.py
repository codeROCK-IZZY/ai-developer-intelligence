"""Read a repository archive (ZIP) into source files, safely.

* **Zip-bomb limits:** number of entries, total uncompressed size, per-file size.
* **No disk writes under archive-controlled names**, so "zip slip" path traversal cannot apply;
  paths are normalised and any entry that escapes the root (``..``) is rejected.
* **Noise is skipped:** VCS folders, dependency and build output (``node_modules``, ``vendor``,
  ``dist``, virtualenvs…), binaries (NUL byte in the first 8 KB), minified bundles.
* The single top-level folder GitHub adds to archives (``repo-main/``) is stripped.
"""

from __future__ import annotations

import hashlib
import io
import posixpath
import zipfile
from dataclasses import dataclass, field

from app.code.languages import detect_language
from app.core.errors import UnprocessableData

SKIP_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        "node_modules",
        "vendor",
        "dist",
        "build",
        "out",
        "target",
        ".venv",
        "venv",
        "env",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        ".next",
        ".nuxt",
        "coverage",
        ".idea",
        ".vscode",
        "bower_components",
        "site-packages",
        ".terraform",
        "__MACOSX",
    }
)


@dataclass(frozen=True)
class SourceFile:
    path: str
    language: str  # python | javascript | typescript | tsx | go | java | text
    content: str
    sha256: str
    size_bytes: int


@dataclass
class ArchiveContents:
    files: list[SourceFile] = field(default_factory=list)
    skipped: dict[str, int] = field(default_factory=dict)  # reason -> count

    def skip(self, reason: str) -> None:
        self.skipped[reason] = self.skipped.get(reason, 0) + 1


def _common_root(names: list[str]) -> str:
    tops = {n.split("/", 1)[0] for n in names}
    if len(tops) == 1 and all("/" in n for n in names):
        return next(iter(tops)) + "/"
    return ""


def read_archive(
    data: bytes, *, max_entries: int, max_total_mb: int, max_file_kb: int, max_files: int
) -> ArchiveContents:
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise UnprocessableData("Not a valid ZIP archive") from exc
    infos = [i for i in z.infolist() if not i.is_dir()]
    if len(infos) > max_entries:
        raise UnprocessableData(f"The archive has more than {max_entries} entries")
    if sum(i.file_size for i in infos) > max_total_mb * 1024 * 1024:
        raise UnprocessableData(f"The archive expands to more than {max_total_mb} MB")
    names = [i.filename.replace("\\", "/") for i in infos]
    for n in names:
        if n.startswith("/") or ".." in n.split("/") or ":" in n.split("/", 1)[0]:
            raise UnprocessableData(f"Unsafe path in archive: {n[:200]}")
    root = _common_root(names)
    out = ArchiveContents()
    for info, raw_name in zip(infos, names, strict=True):
        path = posixpath.normpath(raw_name[len(root) :])
        parts = path.split("/")
        if any(p in SKIP_DIRS for p in parts[:-1]):
            out.skip("dependency, build or VCS folder")
            continue
        language = detect_language(path)
        if language is None:
            out.skip("unsupported file type")
            continue
        if parts[-1].endswith((".min.js", ".min.css", ".bundle.js")) or parts[-1] in ("package-lock.json", "yarn.lock"):
            out.skip("generated or lock file")
            continue
        if info.file_size > max_file_kb * 1024:
            out.skip(f"larger than {max_file_kb} KB")
            continue
        content = z.read(info)
        if b"\x00" in content[:8192]:
            out.skip("binary")
            continue
        text = content.decode("utf-8", errors="replace")
        lines = text.count("\n") + 1
        if language != "text" and len(text) / lines > 400:
            out.skip("minified (very long lines)")
            continue
        if len(out.files) >= max_files:
            out.skip("over the file limit")
            continue
        out.files.append(SourceFile(path, language, text, hashlib.sha256(content).hexdigest(), len(content)))
    out.files.sort(key=lambda f: f.path)
    return out


def build_archive(files: dict[str, str]) -> bytes:
    """Create a ZIP from ``{path: text}`` (used by the demo and the tests)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for path, text in sorted(files.items()):
            # fixed timestamp: the same files always give the same bytes (and the same SHA-256)
            z.writestr(zipfile.ZipInfo(path, date_time=(2026, 1, 1, 0, 0, 0)), text, zipfile.ZIP_DEFLATED)
    return buf.getvalue()
