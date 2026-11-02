"""Supported languages: file detection and the tree-sitter grammar facts the parser needs.

The parser is language-agnostic; everything language-specific is data in ``SPECS``:
which syntax nodes define functions and classes, which are calls, which add a branch to
cyclomatic complexity and which increase nesting depth. Adding a language means adding a
grammar package and one ``LanguageSpec``.
"""

from __future__ import annotations

import posixpath
from dataclasses import dataclass, field
from functools import cache
from typing import Any

CODE_EXTENSIONS = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".mts": "typescript",
    ".tsx": "tsx",
    ".go": "go",
    ".java": "java",
}

# Indexed for search (READMEs, configuration, SQL…) but not parsed into symbols.
TEXT_EXTENSIONS = {
    ".md",
    ".markdown",
    ".rst",
    ".txt",
    ".yaml",
    ".yml",
    ".toml",
    ".json",
    ".cfg",
    ".ini",
    ".sql",
    ".sh",
    ".bash",
    ".html",
    ".css",
    ".scss",
    ".graphql",
    ".proto",
    ".tf",
    ".env.example",
    ".mod",
}
TEXT_FILENAMES = {"dockerfile", "makefile", "readme", "license", "go.mod", "requirements.txt", "procfile"}


def detect_language(path: str) -> str | None:
    """``python`` … ``java`` for parsed code, ``text`` for indexed text files, ``None`` to skip."""
    base = posixpath.basename(path).lower()
    _, ext = posixpath.splitext(base)
    if ext in CODE_EXTENSIONS:
        return CODE_EXTENSIONS[ext]
    if ext in TEXT_EXTENSIONS or base in TEXT_FILENAMES or base.endswith(".env.example"):
        return "text"
    return None


def is_test_path(path: str) -> bool:
    p = path.lower()
    base = posixpath.basename(p)
    return (
        "/tests/" in f"/{p}"
        or "/test/" in f"/{p}"
        or "/__tests__/" in f"/{p}"
        or base.startswith("test_")
        or base.endswith(("_test.py", "_test.go", ".test.ts", ".test.tsx", ".test.js", ".spec.ts", ".spec.js"))
        or base.endswith("test.java")
    )


@dataclass(frozen=True)
class LanguageSpec:
    name: str
    functions: frozenset[str]  # node types that define a function or method
    classes: frozenset[str]  # node types that define a class-like type
    calls: frozenset[str]
    branches: frozenset[str]  # each occurrence adds 1 to cyclomatic complexity
    nesting: frozenset[str]  # control-flow blocks that increase nesting depth
    logical_ops: frozenset[str] = frozenset({"&&", "||", "??"})  # binary operators that add a branch
    comment: str = "comment"
    string_types: frozenset[str] = frozenset({"string"})
    extra: dict[str, Any] = field(default_factory=dict)


_JS_COMMON: dict[str, Any] = {
    "functions": frozenset({"function_declaration", "generator_function_declaration", "method_definition"}),
    "classes": frozenset({"class_declaration", "abstract_class_declaration", "interface_declaration"}),
    "calls": frozenset({"call_expression", "new_expression"}),
    "branches": frozenset(
        {
            "if_statement",
            "for_statement",
            "for_in_statement",
            "while_statement",
            "do_statement",
            "catch_clause",
            "ternary_expression",
            "switch_case",
        }
    ),
    "nesting": frozenset(
        {
            "if_statement",
            "for_statement",
            "for_in_statement",
            "while_statement",
            "do_statement",
            "try_statement",
            "switch_statement",
        }
    ),
    "string_types": frozenset({"string", "template_string"}),
}

SPECS: dict[str, LanguageSpec] = {
    "python": LanguageSpec(
        name="python",
        functions=frozenset({"function_definition"}),
        classes=frozenset({"class_definition"}),
        calls=frozenset({"call"}),
        branches=frozenset(
            {
                "if_statement",
                "elif_clause",
                "for_statement",
                "while_statement",
                "except_clause",
                "conditional_expression",
                "boolean_operator",
                "for_in_clause",
                "if_clause",
                "case_clause",
            }
        ),
        nesting=frozenset(
            {"if_statement", "for_statement", "while_statement", "try_statement", "with_statement", "match_statement"}
        ),
        logical_ops=frozenset(),  # Python uses boolean_operator nodes, counted as branches
    ),
    "javascript": LanguageSpec(name="javascript", **_JS_COMMON),
    "typescript": LanguageSpec(name="typescript", **_JS_COMMON),
    "tsx": LanguageSpec(name="tsx", **_JS_COMMON),
    "go": LanguageSpec(
        name="go",
        functions=frozenset({"function_declaration", "method_declaration"}),
        classes=frozenset({"type_spec"}),
        calls=frozenset({"call_expression"}),
        branches=frozenset({"if_statement", "for_statement", "expression_case", "type_case", "communication_case"}),
        nesting=frozenset(
            {
                "if_statement",
                "for_statement",
                "expression_switch_statement",
                "type_switch_statement",
                "select_statement",
            }
        ),
        logical_ops=frozenset({"&&", "||"}),
        string_types=frozenset({"interpreted_string_literal", "raw_string_literal"}),
    ),
    "java": LanguageSpec(
        name="java",
        functions=frozenset({"method_declaration", "constructor_declaration"}),
        classes=frozenset({"class_declaration", "interface_declaration", "enum_declaration", "record_declaration"}),
        calls=frozenset({"method_invocation", "object_creation_expression"}),
        branches=frozenset(
            {
                "if_statement",
                "for_statement",
                "enhanced_for_statement",
                "while_statement",
                "do_statement",
                "catch_clause",
                "ternary_expression",
                "switch_label",
            }
        ),
        nesting=frozenset(
            {
                "if_statement",
                "for_statement",
                "enhanced_for_statement",
                "while_statement",
                "do_statement",
                "try_statement",
                "switch_expression",
            }
        ),
        logical_ops=frozenset({"&&", "||"}),
        comment="block_comment",
        string_types=frozenset({"string_literal"}),
    ),
}


GRAMMARS = {
    "python": ("tree_sitter_python", "language"),
    "javascript": ("tree_sitter_javascript", "language"),
    "typescript": ("tree_sitter_typescript", "language_typescript"),
    "tsx": ("tree_sitter_typescript", "language_tsx"),
    "go": ("tree_sitter_go", "language"),
    "java": ("tree_sitter_java", "language"),
}


@cache
def ts_parser(language: str) -> Any:
    """A tree-sitter parser for ``language`` (grammars are separate wheels, imported lazily).
    Parsers are not thread-safe; the worker parses in a single thread."""
    import importlib

    import tree_sitter as ts

    if language not in GRAMMARS:
        raise ValueError(f"unsupported language {language}")
    module, func = GRAMMARS[language]
    return ts.Parser(ts.Language(getattr(importlib.import_module(module), func)()))
