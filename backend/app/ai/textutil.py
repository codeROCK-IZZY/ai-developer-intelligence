"""Query-side text utilities for code search."""

from __future__ import annotations

import re

from app.code.chunking import split_identifier

STOPWORDS = frozenset(
    [
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "can",
        "could",
        "do",
        "does",
        "did",
        "for",
        "from",
        "has",
        "have",
        "how",
        "i",
        "if",
        "in",
        "into",
        "is",
        "it",
        "its",
        "me",
        "my",
        "of",
        "on",
        "or",
        "our",
        "should",
        "that",
        "the",
        "their",
        "them",
        "then",
        "there",
        "these",
        "this",
        "those",
        "to",
        "was",
        "we",
        "were",
        "what",
        "when",
        "where",
        "which",
        "who",
        "why",
        "will",
        "with",
        "would",
        "you",
        "your",
        "code",
        "function",
        "method",
        "class",
        "file",
        "files",
        "find",
        "show",
        "tell",
        "defined",
        "define",
        "implemented",
        "implement",
        "implementation",
        "handle",
        "handled",
        "handles",
        "happens",
        "logic",
        "used",
        "use",
        "uses",
        "work",
        "works",
        "get",
        "gets",
    ]
)

_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|\d+")


def query_terms(query: str, limit: int = 24) -> list[str]:
    """Content words of a natural-language or identifier query, with compound identifiers also
    split into their parts (``calculateTax`` → ``calculatetax``, ``calculate``, ``tax``)."""
    out: list[str] = []
    for tok in _TOKEN_RE.findall(query):
        parts = split_identifier(tok)
        whole = tok.lower().replace("_", "")
        for w in ([whole] if len(parts) > 1 else []) + parts:
            if len(w) > 1 and w not in STOPWORDS and w not in out:
                out.append(w)
    return out[:limit]


def identifier_like(query: str) -> list[str]:
    """Tokens in the query that look like code identifiers (snake_case, camelCase, dotted, CONSTANT)."""
    out = []
    for tok in re.findall(r"[A-Za-z_][A-Za-z0-9_.]*", query):
        tok = tok.strip(".")
        if "_" in tok or "." in tok or re.search(r"[a-z][A-Z]", tok) or (tok.isupper() and len(tok) > 2):
            out.append(tok)
    return out
