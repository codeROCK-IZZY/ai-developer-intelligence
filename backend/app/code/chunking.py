"""Split files into search chunks that follow the code's structure.

* One chunk per function or method (long ones are split into overlapping 60-line windows,
  each starting with the signature so the window still says what it belongs to).
* One summary chunk per class: signature, docstring and method signatures.
* Module-level code between symbols (imports, constants, configuration) is chunked in windows.
* Non-code text files (README, YAML, SQL…) are chunked in 40-line windows.

Search text expands identifiers so natural-language queries match code: ``hashPassword`` and
``hash_password`` both also index as ``hash password``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.code.parser import ParsedFile, Symbol

MAX_SYMBOL_LINES = 80
WINDOW = 60
OVERLAP = 10
TEXT_WINDOW = 40
EMBED_CHARS = 1800

_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_CAMEL_RE = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+")


@dataclass
class Chunk:
    start_line: int
    end_line: int
    text: str
    symbol: int | None  # index into the file's symbols
    kind: str  # function | method | class | module | text


def split_identifier(name: str) -> list[str]:
    """``getHTTPResponse_code`` → ['get', 'http', 'response', 'code']."""
    parts: list[str] = []
    for piece in name.split("_"):
        parts.extend(p.lower() for p in _CAMEL_RE.findall(piece))
    return [p for p in parts if p]


def expand_identifiers(text: str) -> str:
    """Original text plus the parts of every compound identifier (for full-text indexing)."""
    extra: list[str] = []
    seen: set[str] = set()
    for ident in _IDENT_RE.findall(text):
        if ident in seen:
            continue
        seen.add(ident)
        parts = split_identifier(ident)
        if len(parts) > 1:
            extra.append(" ".join(parts))
    return text + ("\n" + " ".join(extra) if extra else "")


def _windows(lines: list[str], start: int, end: int, size: int, header: str = "") -> list[tuple[int, int, str]]:
    out = []
    s = start
    while s <= end:
        e = min(end, s + size - 1)
        body = "\n".join(lines[s - 1 : e])
        if header and s != start:
            body = header + "\n    …\n" + body
        out.append((s, e, body))
        if e == end:
            break
        s = e + 1 - OVERLAP
    return out


def chunk_file(language: str, content: str, parsed: ParsedFile | None) -> list[Chunk]:
    lines = content.splitlines()
    n = len(lines)
    if n == 0:
        return []
    if parsed is None:
        return [Chunk(s, e, t, None, "text") for s, e, t in _windows(lines, 1, n, TEXT_WINDOW) if t.strip()]
    chunks: list[Chunk] = []
    covered = [False] * (n + 2)
    symbols = parsed.symbols

    def is_chunk_owner(s: Symbol) -> bool:
        if s.parent is None:
            return True
        return symbols[s.parent].kind == "class"  # methods; nested functions stay in their parent

    for si, s in enumerate(symbols):
        if not is_chunk_owner(s):
            continue
        if s.kind == "class":
            methods = [m for m in symbols if m.parent == si]
            header_end = min(s.end_line, (methods[0].start_line - 1) if methods else s.end_line, s.start_line + 30)
            body = "\n".join(lines[s.start_line - 1 : header_end])
            if methods:
                body += "\n" + "\n".join(f"    {m.signature}" for m in methods)
            chunks.append(Chunk(s.start_line, header_end, body, si, "class"))
            for ln in range(s.start_line, header_end + 1):
                covered[ln] = True
            continue
        for ln in range(s.start_line, s.end_line + 1):
            covered[ln] = True
        if s.loc <= MAX_SYMBOL_LINES:
            chunks.append(Chunk(s.start_line, s.end_line, "\n".join(lines[s.start_line - 1 : s.end_line]), si, s.kind))
        else:
            for ws, we, text in _windows(lines, s.start_line, s.end_line, WINDOW, header=s.signature):
                chunks.append(Chunk(ws, we, text, si, s.kind))
    # module-level regions not covered by any symbol
    ln = 1
    while ln <= n:
        if covered[ln]:
            ln += 1
            continue
        start = ln
        while ln <= n and not covered[ln]:
            ln += 1
        region = lines[start - 1 : ln - 1]
        if sum(1 for x in region if x.strip()) >= 3:
            for ws, we, text in _windows(lines, start, ln - 1, WINDOW):
                chunks.append(Chunk(ws, we, text, None, "module"))
    chunks.sort(key=lambda c: (c.start_line, c.end_line))
    return chunks


def embedding_text(path: str, chunk: Chunk, symbol: Symbol | None) -> str:
    """What the embedding model sees: location and documentation first, then the code."""
    head = [path]
    if symbol is not None:
        head.append(f"{symbol.kind} {symbol.qualified_name} ({' '.join(split_identifier(symbol.name))})")
        if symbol.docstring:
            head.append(symbol.docstring)
    return ("\n".join(head) + "\n" + chunk.text)[:EMBED_CHARS]


def search_text(path: str, chunk: Chunk, symbol: Symbol | None) -> str:
    """What the full-text index sees: path words, symbol name parts, docstring, expanded code."""
    path_words = " ".join(split_identifier(re.sub(r"\.[a-z]+$", "", path).replace("/", "_")))
    head = [path_words]
    if symbol is not None:
        head.append(" ".join(split_identifier(symbol.qualified_name.replace(".", "_"))))
        head.append(symbol.docstring)
    return expand_identifiers("\n".join(head) + "\n" + chunk.text)[:20000]
