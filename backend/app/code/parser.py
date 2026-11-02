"""Parse one source file with tree-sitter into symbols, calls, imports and markers.

One iterative walk over the syntax tree (no recursion, so deeply nested expressions cannot
overflow the stack) collects:

* **Symbols** — functions, methods, classes (and Go struct/interface types, Java enums…),
  with line range, signature, docstring, decorators, export status, parameter count,
  cyclomatic complexity and maximum nesting depth.
* **Calls** — callee name, receiver expression (``self``, ``passwords``, ``api``…), the
  enclosing symbol, and whether the first argument is a dynamically built string (used by
  the SQL-injection rule).
* **Imports** — module, imported names and aliases, relative level (Python).
* **Markers** — facts that rules turn into findings: swallowed exceptions, TODO comments,
  ``dangerouslySetInnerHTML``/``innerHTML`` writes, ``if __name__ == "__main__"`` blocks.

Cyclomatic complexity = 1 + decision points (if/elif, loops, except/catch, case labels,
conditional expressions, ``and``/``or``/``&&``/``||``) inside the function, not counting
nested functions, which get their own score.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any

from app.code.languages import SPECS, LanguageSpec, ts_parser

TODO_RE = re.compile(r"\b(TODO|FIXME|HACK|XXX)\b[:\s]*(.*)")
LITERAL_TYPES = frozenset(
    {
        "string",
        "string_content",
        "string_fragment",
        "integer",
        "float",
        "number",
        "int_literal",
        "float_literal",
        "interpreted_string_literal_content",
        "raw_string_literal",
        "decimal_integer_literal",
        "string_literal",
        "template_string",
        "true",
        "false",
        "none",
        "null",
        "nil",
        "character_literal",
        "escape_sequence",
    }
)
IDENT_TYPES = frozenset(
    {
        "identifier",
        "property_identifier",
        "field_identifier",
        "type_identifier",
        "shorthand_property_identifier",
        "package_identifier",
    }
)


@dataclass
class Symbol:
    kind: str  # function | method | class
    name: str
    qualified_name: str  # within the file, e.g. "PaymentGateway.charge"
    start_line: int
    end_line: int
    signature: str
    docstring: str
    exported: bool
    decorators: list[str]
    params: int
    parent: int | None
    complexity: int = 1
    max_nesting: int = 0
    body_hash: str = ""
    tokens: list[str] = field(default_factory=list, repr=False)  # normalised tokens (clone detection)

    @property
    def loc(self) -> int:
        return self.end_line - self.start_line + 1


@dataclass
class Call:
    caller: int | None  # index into symbols; None = module level
    name: str
    receiver: str | None
    line: int
    kind: str  # call | new
    dynamic_arg: str | None  # interpolated | concat | format — first argument built from data
    text: str


@dataclass
class Import:
    module: str
    names: list[tuple[str, str]]  # (imported name, local name)
    alias: str | None  # local name for the module itself (import x as y, import * as y, Go alias)
    level: int  # Python relative-import level (from .. import x → 2)
    line: int


@dataclass
class Marker:
    kind: str  # swallowed-exception | todo | dangerous-html | entry-point
    line: int
    symbol: int | None
    text: str


@dataclass
class ParsedFile:
    language: str
    symbols: list[Symbol] = field(default_factory=list)
    calls: list[Call] = field(default_factory=list)
    imports: list[Import] = field(default_factory=list)
    markers: list[Marker] = field(default_factory=list)
    syntax_errors: int = 0
    package: str | None = None  # Go package / Java package declaration


def _text(node: Any) -> str:
    return str(node.text.decode("utf-8", errors="replace"))


def _collapse(s: str, limit: int = 240) -> str:
    s = re.sub(r"\s+", " ", s).strip()
    return s if len(s) <= limit else s[: limit - 1] + "…"


def _clean_comment(raw: str) -> str:
    lines = []
    for line in raw.splitlines():
        line = line.strip()
        line = re.sub(r"^(/\*\*?|\*/|\*|//+|#)\s?", "", line)
        line = re.sub(r"\*/$", "", line).strip()
        if line:
            lines.append(line)
    return " ".join(lines)


class _Walker:
    def __init__(self, language: str, source: bytes):
        self.lang = language
        self.spec: LanguageSpec = SPECS[language]
        self.src = source
        self.out = ParsedFile(language=language)

    # ------------------------------------------------------------------ symbols
    def _definition(self, node: Any) -> tuple[str, str] | None:
        """(kind, name) if ``node`` defines a symbol, else None."""
        t = node.type
        spec = self.spec
        if t in spec.functions:
            name_node = node.child_by_field_name("name")
            if name_node is None:
                return None
            kind = (
                "method" if t in ("method_definition", "method_declaration", "constructor_declaration") else "function"
            )
            return kind, _text(name_node)
        if t in spec.classes:
            if t == "type_spec":
                typ = node.child_by_field_name("type")
                if typ is None or typ.type not in ("struct_type", "interface_type"):
                    return None
            name_node = node.child_by_field_name("name")
            return ("class", _text(name_node)) if name_node is not None else None
        if t == "variable_declarator" and self.lang in ("javascript", "typescript", "tsx"):
            value = node.child_by_field_name("value")
            name_node = node.child_by_field_name("name")
            if (
                value is not None
                and name_node is not None
                and name_node.type == "identifier"
                and value.type in ("arrow_function", "function_expression", "function")
            ):
                return "function", _text(name_node)
        return None

    def _exported(self, node: Any, name: str, parent: Symbol | None) -> bool:
        if self.lang == "python":
            return not name.startswith("_") and (parent is None or parent.exported)
        if self.lang == "go":
            return name[:1].isupper()
        if self.lang == "java":
            mods = next((c for c in node.children if c.type == "modifiers"), None)
            return mods is not None and ("public" in _text(mods) or "protected" in _text(mods))
        # JavaScript / TypeScript: inside an export statement, or a member of an exported class
        n = node
        for _ in range(3):
            n = n.parent
            if n is None:
                break
            if n.type == "export_statement":
                return True
        return parent is not None and parent.exported and not name.startswith("#")

    def _outer(self, node: Any) -> Any:
        """The node that owns a definition's leading comments/decorators."""
        n = node
        if n.type in ("variable_declarator", "type_spec") and n.parent is not None:
            n = n.parent  # lexical_declaration / Go type_declaration
        while n.parent is not None and n.parent.type in ("export_statement", "decorated_definition"):
            n = n.parent
        return n

    def _docstring(self, node: Any) -> str:
        if self.lang == "python":
            body = node.child_by_field_name("body")
            first = body.named_children[0] if body is not None and body.named_children else None
            if first is not None and first.type == "expression_statement" and first.named_children:
                s = first.named_children[0]
                if s.type == "string":
                    content = "".join(_text(c) for c in s.named_children if c.type == "string_content")
                    return _collapse(content, 600)
            return ""
        outer = self._outer(node)
        parts: list[str] = []
        prev = outer.prev_named_sibling
        expected_row = outer.start_point[0]
        while prev is not None and prev.type in ("comment", "block_comment", "line_comment"):
            if prev.end_point[0] < expected_row - 1:
                break
            parts.insert(0, _clean_comment(_text(prev)))
            expected_row = prev.start_point[0]
            prev = prev.prev_named_sibling
        return _collapse(" ".join(parts), 600)

    def _signature(self, node: Any) -> str:
        body = node.child_by_field_name("body")
        if node.type == "variable_declarator":
            value = node.child_by_field_name("value")
            body = value.child_by_field_name("body") if value is not None else None
        end = body.start_byte if body is not None else node.end_byte
        raw = self.src[node.start_byte : end].decode("utf-8", errors="replace").rstrip()
        if raw.endswith("=>"):
            raw = raw[:-2].rstrip()
        raw = raw.rstrip(":{ \t\n")
        if node.type == "variable_declarator":
            raw += " =>"
        return _collapse(raw)

    def _params(self, node: Any) -> int:
        target = node
        if node.type == "variable_declarator":
            target = node.child_by_field_name("value") or node
        params = target.child_by_field_name("parameters")
        if params is None:
            return 0
        names = [c for c in params.named_children if c.type not in ("comment", "line_comment", "block_comment")]
        if self.lang == "python":
            names = [c for c in names if _text(c) not in ("self", "cls")]
        return len(names)

    def _decorators(self, node: Any) -> list[str]:
        p = node.parent
        if self.lang == "python" and p is not None and p.type == "decorated_definition":
            return [_collapse(_text(d).lstrip("@"), 120) for d in p.children if d.type == "decorator"]
        if self.lang == "java":
            mods = next((c for c in node.children if c.type == "modifiers"), None)
            if mods is not None:
                return [_collapse(_text(a).lstrip("@"), 120) for a in mods.named_children if "annotation" in a.type]
        if self.lang in ("typescript", "tsx"):
            return [_collapse(_text(d).lstrip("@"), 120) for d in node.children if d.type == "decorator"]
        return []

    def _add_symbol(self, node: Any, kind: str, name: str, parent_idx: int | None) -> int:
        parent = self.out.symbols[parent_idx] if parent_idx is not None else None
        if kind == "function" and parent is not None and parent.kind == "class":
            kind = "method"
        qual_parent = parent.qualified_name if parent is not None else None
        if node.type == "method_declaration" and self.lang == "go":
            recv = node.child_by_field_name("receiver")
            types = [t for t in _iter(recv) if t.type == "type_identifier"] if recv is not None else []
            if types:
                qual_parent = _text(types[0])
        outer = self._outer(node) if self.lang == "python" else node
        sym = Symbol(
            kind=kind,
            name=name,
            qualified_name=f"{qual_parent}.{name}" if qual_parent else name,
            start_line=outer.start_point[0] + 1,
            end_line=node.end_point[0] + 1,
            signature=self._signature(node),
            docstring=self._docstring(node),
            exported=self._exported(node, name, parent),
            decorators=self._decorators(node),
            params=self._params(node) if kind != "class" else 0,
            parent=parent_idx,
            body_hash=hashlib.sha1(re.sub(rb"\s+", b" ", node.text).strip()).hexdigest(),  # noqa: S324
        )
        if kind != "class":
            sym.tokens = _normalised_tokens(node)
        self.out.symbols.append(sym)
        return len(self.out.symbols) - 1

    # ------------------------------------------------------------------ calls
    def _receiver(self, node: Any) -> str | None:
        if node is None:
            return None
        if node.type in (
            "identifier",
            "this",
            "self",
            "attribute",
            "member_expression",
            "selector_expression",
            "field_access",
            "scoped_identifier",
        ):
            t = _text(node)
            return t if len(t) <= 80 and "(" not in t and "\n" not in t else "<expr>"
        return "<expr>"

    def _call(self, node: Any, caller: int | None) -> None:
        t = node.type
        receiver: str | None = None
        name: str | None = None
        kind = "call"
        if t in ("call", "call_expression"):
            fn = node.child_by_field_name("function")
            if fn is None:
                return
            if fn.type in ("identifier",):
                name = _text(fn)
            elif fn.type == "attribute":
                name = _text(fn.child_by_field_name("attribute"))
                receiver = self._receiver(fn.child_by_field_name("object"))
            elif fn.type == "member_expression":
                prop = fn.child_by_field_name("property")
                name = _text(prop) if prop is not None else None
                receiver = self._receiver(fn.child_by_field_name("object"))
            elif fn.type == "selector_expression":
                name = _text(fn.child_by_field_name("field"))
                receiver = self._receiver(fn.child_by_field_name("operand"))
        elif t == "method_invocation":
            name = _text(node.child_by_field_name("name"))
            receiver = self._receiver(node.child_by_field_name("object"))
        elif t in ("new_expression", "object_creation_expression"):
            target = node.child_by_field_name("constructor") or node.child_by_field_name("type")
            if target is not None:
                name = re.sub(r"<.*", "", _text(target)).split(".")[-1]
            kind = "new"
        if not name:
            return
        args = node.child_by_field_name("arguments")
        first = args.named_children[0] if args is not None and args.named_children else None
        self.out.calls.append(
            Call(
                caller=caller,
                name=name,
                receiver=receiver,
                line=node.start_point[0] + 1,
                kind=kind,
                dynamic_arg=self._dynamic_string(first) if first is not None else None,
                text=_collapse(_text(node), 300),
            )
        )

    def _dynamic_string(self, node: Any) -> str | None:
        """Is this argument a string assembled from other values (f-string, concatenation, format)?"""
        t = node.type
        if t == "keyword_argument":
            return None
        if t == "string" and any(c.type == "interpolation" for c in node.children):
            return "interpolated"
        if t == "template_string" and any(c.type == "template_substitution" for c in node.children):
            return "interpolated"
        if t in ("binary_operator", "binary_expression"):
            op = node.child_by_field_name("operator")
            if op is not None and _text(op) in ("+", "%"):
                leaves = [node.child_by_field_name("left"), node.child_by_field_name("right")]
                if any(n is not None and n.type in self.spec.string_types for n in leaves) and _has_variable(node):
                    return "concat"
        if t in ("call", "call_expression", "method_invocation"):
            txt = _text(node)
            if re.match(r"(fmt\.Sprintf|String\.format)\(", txt) or re.search(r"""["']\s*\.format\(""", txt):
                return "format"
        return None

    # ------------------------------------------------------------------ imports
    def _import(self, node: Any) -> None:
        line = node.start_point[0] + 1
        imports = self.out.imports
        t = node.type
        if self.lang == "python":
            if t == "import_statement":
                for c in node.named_children:
                    if c.type == "dotted_name":
                        imports.append(Import(_text(c), [], None, 0, line))
                    elif c.type == "aliased_import":
                        imports.append(
                            Import(
                                _text(c.child_by_field_name("name")), [], _text(c.child_by_field_name("alias")), 0, line
                            )
                        )
            elif t == "import_from_statement":
                mod = node.child_by_field_name("module_name")
                level = 0
                module = ""
                if mod is not None and mod.type == "relative_import":
                    prefix = next((c for c in mod.children if c.type == "import_prefix"), None)
                    level = len(_text(prefix)) if prefix is not None else 1
                    dn = next((c for c in mod.children if c.type == "dotted_name"), None)
                    module = _text(dn) if dn is not None else ""
                elif mod is not None:
                    module = _text(mod)
                names: list[tuple[str, str]] = []
                for c in node.children_by_field_name("name"):
                    if c.type == "aliased_import":
                        names.append((_text(c.child_by_field_name("name")), _text(c.child_by_field_name("alias"))))
                    else:
                        n = _text(c)
                        names.append((n, n.split(".")[-1]))
                if any(c.type == "wildcard_import" for c in node.children):
                    names.append(("*", "*"))
                imports.append(Import(module, names, None, level, line))
            return
        if self.lang in ("javascript", "typescript", "tsx"):
            src = node.child_by_field_name("source")
            if src is None:
                return
            module = _text(src).strip("'\"`")
            names = []
            alias = None
            clause = next((c for c in node.named_children if c.type == "import_clause"), None)
            for c in clause.named_children if clause is not None else []:
                if c.type == "identifier":
                    names.append(("default", _text(c)))
                elif c.type == "namespace_import":
                    alias = _text(c.named_children[-1])
                elif c.type == "named_imports":
                    for spec in c.named_children:
                        if spec.type == "import_specifier":
                            n = _text(spec.child_by_field_name("name"))
                            a = spec.child_by_field_name("alias")
                            names.append((n, _text(a) if a is not None else n))
            imports.append(Import(module, names, alias, 0, line))
            return
        if self.lang == "go":
            for spec in (n for n in _iter(node) if n.type == "import_spec"):
                path = spec.child_by_field_name("path")
                alias_node = spec.child_by_field_name("name")
                if path is None:
                    continue
                module = _text(path).strip('"`')
                alias = _text(alias_node) if alias_node is not None else module.rsplit("/", 1)[-1]
                imports.append(Import(module, [], alias, 0, spec.start_point[0] + 1))
            return
        if self.lang == "java":
            txt = _text(node).removeprefix("import").strip().rstrip(";").strip()
            static = txt.startswith("static ")
            txt = txt.removeprefix("static ").strip()
            if txt.endswith(".*"):
                imports.append(Import(txt[:-2], [("*", "*")], None, 0, line))
            else:
                mod, _, last = txt.rpartition(".")
                imports.append(Import(mod if static else txt, [(last, last)] if static else [], None, 0, line))

    # ------------------------------------------------------------------ walk
    def run(self, tree: Any) -> ParsedFile:
        spec = self.spec
        root = tree.root_node
        if self.lang in ("go", "java"):
            pkg = next((c for c in root.named_children if c.type in ("package_clause", "package_declaration")), None)
            if pkg is not None:
                self.out.package = _text(pkg.named_children[-1]) if pkg.named_children else None
        # stack items: (node, enclosing symbol, enclosing function, nesting depth within that function)
        stack: list[tuple[Any, int | None, int | None, int]] = [(root, None, None, 0)]
        import_types = {
            "python": ("import_statement", "import_from_statement"),
            "go": ("import_declaration",),
            "java": ("import_declaration",),
        }.get(self.lang, ("import_statement",))
        while stack:
            node, sym, fn, depth = stack.pop()
            t = node.type
            if node.is_error or node.is_missing:
                self.out.syntax_errors += 1
            definition = self._definition(node)
            if definition is not None:
                idx = self._add_symbol(node, definition[0], definition[1], sym)
                sym = idx
                if self.out.symbols[idx].kind != "class":
                    fn, depth = idx, 0
            elif t in import_types:
                self._import(node)
                continue
            elif t in spec.calls:
                self._call(node, sym)
            elif t == spec.comment or t in ("comment", "line_comment", "block_comment"):
                m = TODO_RE.search(_text(node))
                if m:
                    self.out.markers.append(Marker("todo", node.start_point[0] + 1, sym, _collapse(m.group(0), 160)))
                continue
            if fn is not None:
                f = self.out.symbols[fn]
                if t in spec.branches:
                    f.complexity += 1
                elif t == "binary_expression":
                    op = node.child_by_field_name("operator")
                    if op is not None and _text(op) in spec.logical_ops:
                        f.complexity += 1
            if t in spec.nesting:
                depth += 1
                if fn is not None and t != "elif_clause":
                    f = self.out.symbols[fn]
                    f.max_nesting = max(f.max_nesting, depth)
            self._markers(node, sym)
            for child in reversed(node.children):
                stack.append((child, sym, fn, depth))
        return self.out

    def _markers(self, node: Any, sym: int | None) -> None:
        t = node.type
        line = node.start_point[0] + 1
        if t == "except_clause":
            block = next((c for c in node.named_children if c.type == "block"), None)
            stmts = block.named_children if block is not None else []
            if stmts and all(s.type in ("pass_statement", "comment") or _text(s) == "..." for s in stmts):
                self.out.markers.append(Marker("swallowed-exception", line, sym, _collapse(_text(node), 120)))
        elif t == "catch_clause" and self.lang in ("javascript", "typescript", "tsx", "java"):
            body = node.child_by_field_name("body") or next(
                (c for c in node.named_children if c.type in ("statement_block", "block")), None
            )
            if body is not None and not [c for c in body.named_children if "comment" not in c.type]:
                self.out.markers.append(Marker("swallowed-exception", line, sym, _collapse(_text(node), 120)))
        elif (
            t == "jsx_attribute" and node.named_children and _text(node.named_children[0]) == "dangerouslySetInnerHTML"
        ):
            self.out.markers.append(Marker("dangerous-html", line, sym, _collapse(_text(node), 120)))
        elif t == "assignment_expression":
            left = node.child_by_field_name("left")
            if left is not None and left.type == "member_expression":
                prop = left.child_by_field_name("property")
                if prop is not None and _text(prop) in ("innerHTML", "outerHTML"):
                    self.out.markers.append(Marker("dangerous-html", line, sym, _collapse(_text(node), 120)))
        elif t == "if_statement" and self.lang == "python" and node.parent is not None and node.parent.type == "module":
            cond = node.child_by_field_name("condition")
            if cond is not None and "__name__" in _text(cond) and "__main__" in _text(cond):
                self.out.markers.append(Marker("entry-point", line, None, "if __name__ == '__main__'"))


def _iter(node: Any) -> Any:
    stack = [node]
    while stack:
        n = stack.pop()
        yield n
        stack.extend(reversed(n.children))


def _has_variable(node: Any) -> bool:
    """Does an expression use a value other than literals and UPPER_CASE constants? Joining
    constants (``"SELECT " + COLUMNS``) is safe; joining variables or call results is not."""
    for n in _iter(node):
        if n.type in ("call", "call_expression", "subscript", "subscript_expression"):
            return True
        if n.type in IDENT_TYPES and not _text(n).isupper():
            return True
    return False


def _normalised_tokens(node: Any) -> list[str]:
    """Leaf tokens with identifiers → ``$`` and literals → ``#`` (detects renamed copies)."""
    out: list[str] = []
    stack = [node]
    while stack:
        n = stack.pop()
        t = n.type
        if "comment" in t:
            continue
        if t in LITERAL_TYPES:
            out.append("#")
            continue
        if n.child_count == 0:
            out.append("$" if t in IDENT_TYPES else _text(n))
            continue
        stack.extend(reversed(n.children))
    return out


def parse_source(language: str, source: str | bytes) -> ParsedFile:
    data = source.encode("utf-8") if isinstance(source, str) else source
    tree = ts_parser(language).parse(data)
    # Pre-order traversal appends symbols in source order; parent/caller indices refer to it.
    return _Walker(language, data).run(tree)
