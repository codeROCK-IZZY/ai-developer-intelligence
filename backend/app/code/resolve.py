"""Resolve imports to files and calls to symbols across a whole snapshot.

Static resolution without running or type-checking the code, so it is deliberately
conservative. Each resolved call records *how* it was resolved:

* ``local``   — a function/class defined in the same file (or nested in the caller)
* ``self``    — ``self.x()`` / ``this.x()`` → a method of the enclosing class
* ``import``  — through an import binding (``from a import f``, ``import * as api``, Go package alias…)
* ``package`` — Go: a function in another file of the same package
* ``unique-method`` — ``obj.method()`` where exactly one in-repo method has that (distinctive)
  name; a heuristic, reported separately so its precision can be measured

Anything else stays unresolved (library calls, dynamic dispatch); unresolved calls are
kept, by name, so text-based features still see them.
"""

from __future__ import annotations

import posixpath
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field

from app.code.parser import ParsedFile, Symbol

SymRef = tuple[int, int]  # (file index, symbol index)

JS_EXTS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")
NODE_BUILTINS = frozenset(
    {
        "fs",
        "path",
        "http",
        "https",
        "crypto",
        "os",
        "url",
        "util",
        "stream",
        "events",
        "child_process",
        "net",
        "zlib",
        "buffer",
        "assert",
        "querystring",
        "readline",
        "worker_threads",
        "timers",
        "dns",
        "tls",
    }
)
# Method names too generic for the unique-method heuristic (library and collection methods).
COMMON_METHODS = frozenset(
    {
        "get",
        "set",
        "add",
        "put",
        "pop",
        "push",
        "append",
        "extend",
        "insert",
        "remove",
        "delete",
        "update",
        "items",
        "keys",
        "values",
        "copy",
        "clear",
        "find",
        "filter",
        "map",
        "reduce",
        "join",
        "split",
        "strip",
        "format",
        "read",
        "write",
        "close",
        "open",
        "send",
        "recv",
        "run",
        "start",
        "stop",
        "call",
        "apply",
        "bind",
        "then",
        "catch",
        "query",
        "execute",
        "commit",
        "rollback",
        "flush",
        "save",
        "load",
        "dump",
        "dumps",
        "loads",
        "encode",
        "decode",
        "lower",
        "upper",
        "replace",
        "sort",
        "count",
        "index",
        "next",
        "emit",
        "on",
        "off",
        "log",
        "info",
        "warn",
        "warning",
        "error",
        "debug",
        "toString",
        "equals",
        "hash",
        "size",
        "length",
        "len",
        "contains",
        "has",
        "parse",
        "render",
        "handle",
        "process",
        "validate",
        "init",
        "create",
        "build",
        "connect",
        "fetch",
        "json",
        "text",
        "list",
        "scalar",
        "scalars",
    }
)


@dataclass
class FileInput:
    path: str
    language: str
    parsed: ParsedFile | None  # None for text files
    content: str


@dataclass
class ImportResolution:
    file_idx: int
    import_idx: int
    kind: str  # internal | stdlib | external | unresolved
    targets: list[int] = field(default_factory=list)  # file indices
    package: str | None = None


@dataclass
class CallResolution:
    file_idx: int
    call_idx: int
    target: SymRef | None
    resolution: str | None


@dataclass
class Binding:
    kind: str  # module | symbol | external
    files: list[int] = field(default_factory=list)
    symbol: SymRef | None = None
    package: str | None = None
    name: str | None = None  # the imported name, for symbol bindings


@dataclass
class Resolved:
    imports: list[ImportResolution]
    calls: list[CallResolution]
    bindings: list[dict[str, Binding]]


class _Index:
    def __init__(self, files: list[FileInput]):
        self.files = files
        self.by_path = {f.path: i for i, f in enumerate(files)}
        self.py_modules: dict[str, int] = {}
        for i, f in enumerate(files):
            if f.language == "python":
                mod = f.path[:-3].replace("/", ".")
                if mod.endswith(".__init__"):
                    mod = mod[: -len(".__init__")]
                elif mod == "__init__":
                    mod = ""
                self.py_modules[mod] = i
        self.go_modules: list[tuple[str, str]] = []  # (module path, root dir)
        for f in files:
            if posixpath.basename(f.path) == "go.mod":
                m = re.search(r"^module\s+(\S+)", f.content, re.MULTILINE)
                if m:
                    self.go_modules.append((m.group(1), posixpath.dirname(f.path)))
        self.dir_files: dict[tuple[str, str], list[int]] = defaultdict(list)
        for i, f in enumerate(files):
            self.dir_files[(posixpath.dirname(f.path), f.language)].append(i)
        self.top_level: list[dict[str, int]] = []
        self.methods_by_name: dict[str, list[SymRef]] = defaultdict(list)
        for i, f in enumerate(files):
            tl: dict[str, int] = {}
            for j, s in enumerate(f.parsed.symbols if f.parsed else []):
                if s.parent is None:
                    tl.setdefault(s.name, j)
                if s.kind == "method":
                    self.methods_by_name[s.name].append((i, j))
            self.top_level.append(tl)

    # ------------------------------------------------------------------ python
    def py_lookup(self, module: str) -> int | None:
        if module in self.py_modules:
            return self.py_modules[module]
        matches = [i for m, i in self.py_modules.items() if m.endswith("." + module)]
        return matches[0] if len(matches) == 1 else None

    def py_package(self, file_idx: int) -> list[str]:
        f = self.files[file_idx]
        parts = f.path[:-3].split("/")
        return parts[:-1]  # __init__.py's package is its directory, same as any sibling module

    # ------------------------------------------------------------------ javascript
    def js_lookup(self, from_path: str, spec: str) -> int | None:
        if spec.startswith("@/"):
            rest = spec[2:]
            cands = [p for p in self.by_path if "/src/" + rest in "/" + p or p.startswith("src/" + rest)]
            base = None
            for c in cands:
                stem = c.rsplit(".", 1)[0]
                if stem.endswith("src/" + rest) or stem.endswith(f"src/{rest}/index"):
                    base = c
                    break
            return self.by_path.get(base) if base else None
        base = posixpath.normpath(posixpath.join(posixpath.dirname(from_path), spec))
        stem = base.rsplit(".", 1)[0] if base.endswith((".js", ".jsx", ".mjs")) else base
        for cand in [base, *(stem + e for e in JS_EXTS), *(f"{base}/index{e}" for e in JS_EXTS)]:
            if cand in self.by_path:
                return self.by_path[cand]
        return None


def _package_name(language: str, module: str) -> str:
    if language in ("javascript", "typescript", "tsx"):
        parts = module.split("/")
        return "/".join(parts[:2]) if module.startswith("@") else parts[0]
    if language == "go":
        parts = module.split("/")
        return "/".join(parts[:3]) if "." in parts[0] else parts[0]
    if language == "java":
        return ".".join(module.split(".")[:3])
    return module.split(".")[0]


def resolve(files: list[FileInput]) -> Resolved:
    idx = _Index(files)
    imports: list[ImportResolution] = []
    bindings: list[dict[str, Binding]] = [{} for _ in files]

    for fi, f in enumerate(files):
        if f.parsed is None:
            continue
        lang = f.language
        for ii, imp in enumerate(f.parsed.imports):
            res = ImportResolution(fi, ii, "unresolved")
            b = bindings[fi]
            if lang == "python":
                if imp.level:
                    pkg = idx.py_package(fi)
                    base = pkg[: len(pkg) - (imp.level - 1)] if imp.level > 1 else pkg
                    module = ".".join([*base, *([imp.module] if imp.module else [])])
                else:
                    module = imp.module
                target = idx.py_lookup(module) if module or imp.level else None
                if not imp.names:  # import a.b.c [as x]
                    if target is not None:
                        res.kind, res.targets = "internal", [target]
                        b[imp.alias or imp.module] = Binding("module", [target])
                    else:
                        top = imp.module.split(".")[0]
                        res.kind = "stdlib" if top in sys.stdlib_module_names else "external"
                        res.package = top
                        b[imp.alias or top] = Binding("external", package=top)
                        b[imp.alias or imp.module] = Binding("external", package=top)
                else:
                    any_internal = False
                    for name, local in imp.names:
                        sub = idx.py_lookup(f"{module}.{name}") if module else idx.py_lookup(name)
                        if sub is not None:
                            b[local] = Binding("module", [sub])
                            res.targets.append(sub)
                            any_internal = True
                        elif target is not None and name != "*":
                            b[local] = Binding(
                                "symbol", [target], symbol=_lookup_symbol(idx, bindings, target, name), name=name
                            )
                            any_internal = True
                    if target is not None and target not in res.targets:
                        res.targets.insert(0, target)
                    if any_internal or target is not None:
                        res.kind = "internal"
                    elif not imp.level:
                        top = module.split(".")[0]
                        res.kind = "stdlib" if top in sys.stdlib_module_names else "external"
                        res.package = top
                        for _, local in imp.names:
                            b[local] = Binding("external", package=top)
            elif lang in ("javascript", "typescript", "tsx"):
                relative = imp.module.startswith((".", "@/"))
                target = idx.js_lookup(f.path, imp.module) if relative else None
                if target is not None:
                    res.kind, res.targets = "internal", [target]
                    if imp.alias:
                        b[imp.alias] = Binding("module", [target])
                    for name, local in imp.names:
                        lookup = local if name == "default" else name
                        b[local] = Binding(
                            "symbol", [target], symbol=_lookup_symbol(idx, bindings, target, lookup), name=lookup
                        )
                elif not relative:
                    bare = imp.module.removeprefix("node:")
                    res.kind = "stdlib" if bare.split("/")[0] in NODE_BUILTINS else "external"
                    res.package = _package_name(lang, imp.module)
                    for alias_name in [imp.alias, *(lc for _, lc in imp.names)]:
                        if alias_name:
                            b[alias_name] = Binding("external", package=res.package)
            elif lang == "go":
                targets: list[int] = []
                for mod_path, root in idx.go_modules:
                    if imp.module == mod_path or imp.module.startswith(mod_path + "/"):
                        rel = imp.module[len(mod_path) :].lstrip("/")
                        d = posixpath.normpath(posixpath.join(root, rel)) if rel or root else ""
                        d = "" if d == "." else d
                        targets = list(idx.dir_files.get((d, "go"), []))
                        break
                if targets:
                    res.kind, res.targets = "internal", targets
                    if imp.alias:
                        b[imp.alias] = Binding("module", targets)
                else:
                    first = imp.module.split("/")[0]
                    res.kind = "external" if "." in first else "stdlib"
                    res.package = _package_name(lang, imp.module)
                    if imp.alias:
                        b[imp.alias] = Binding("external", package=res.package)
            elif lang == "java":
                is_wild = any(n == "*" for n, _ in imp.names)
                rel = imp.module.replace(".", "/")
                if is_wild:
                    targets = [i for p, i in idx.by_path.items() if posixpath.dirname(p).endswith(rel)]
                else:
                    targets = [i for p, i in idx.by_path.items() if p.endswith(rel + ".java")]
                if targets:
                    res.kind, res.targets = "internal", targets
                    if not is_wild:
                        cls = imp.module.rsplit(".", 1)[-1]
                        b[cls] = Binding("symbol", targets, symbol=_lookup_symbol(idx, bindings, targets[0], cls))
                else:
                    root = imp.module.split(".")[0]
                    res.kind = "stdlib" if root in ("java", "javax", "jdk", "sun") else "external"
                    res.package = _package_name(lang, imp.module)
            imports.append(res)

    # Second pass: names re-exported by a module processed later (e.g. a package __init__).
    for b in bindings:
        for binding in b.values():
            if binding.kind == "symbol" and binding.symbol is None and binding.files and binding.name:
                binding.symbol = _lookup_symbol(idx, bindings, binding.files[0], binding.name)

    calls: list[CallResolution] = []
    for fi, f in enumerate(files):
        if f.parsed is None:
            continue
        for ci, call in enumerate(f.parsed.calls):
            sym_target, how = _resolve_call(idx, bindings, fi, call.caller, call.name, call.receiver, call.kind)
            calls.append(CallResolution(fi, ci, sym_target, how))
    return Resolved(imports=imports, calls=calls, bindings=bindings)


def _lookup_symbol(idx: _Index, bindings: list[dict[str, Binding]], file_idx: int, name: str) -> SymRef | None:
    j = idx.top_level[file_idx].get(name)
    if j is not None:
        return (file_idx, j)
    b = bindings[file_idx].get(name)  # re-exported (e.g. from a package __init__)
    return b.symbol if b is not None and b.kind == "symbol" else None


def _enclosing_class(symbols: list[Symbol], caller: int | None) -> int | None:
    i = caller
    while i is not None:
        if symbols[i].kind == "class":
            return i
        i = symbols[i].parent
    return None


def _method_of(symbols: list[Symbol], cls: int, name: str) -> int | None:
    return next((j for j, s in enumerate(symbols) if s.parent == cls and s.name == name), None)


def _resolve_call(
    idx: _Index,
    bindings: list[dict[str, Binding]],
    fi: int,
    caller: int | None,
    name: str,
    receiver: str | None,
    kind: str,
) -> tuple[SymRef | None, str | None]:
    f = idx.files[fi]
    assert f.parsed is not None
    symbols = f.parsed.symbols
    b = bindings[fi]
    if receiver is None:
        # nested function defined inside the caller
        if caller is not None:
            nested = next((j for j, s in enumerate(symbols) if s.parent == caller and s.name == name), None)
            if nested is not None:
                return (fi, nested), "local"
        j = idx.top_level[fi].get(name)
        if j is not None:
            return (fi, j), "local"
        if f.language == "java":
            cls = _enclosing_class(symbols, caller)
            if cls is not None:
                m = _method_of(symbols, cls, name)
                if m is not None:
                    return (fi, m), "self"
        binding = b.get(name)
        if binding is not None and binding.kind == "symbol" and binding.symbol is not None:
            return binding.symbol, "import"
        if f.language == "go":
            for other in idx.dir_files.get((posixpath.dirname(f.path), "go"), []):
                j = idx.top_level[other].get(name)
                if j is not None and other != fi:
                    return (other, j), "package"
        return None, None
    if receiver in ("self", "this", "cls"):
        cls = _enclosing_class(symbols, caller)
        if cls is not None:
            m = _method_of(symbols, cls, name)
            if m is not None:
                return (fi, m), "self"
        return None, None
    binding = b.get(receiver)
    if binding is not None:
        if binding.kind == "module":
            for target in binding.files:
                j = idx.top_level[target].get(name)
                if j is not None:
                    return (target, j), "import"
            return None, None
        if binding.kind == "symbol" and binding.symbol is not None:  # Class.static_method()
            tf, tj = binding.symbol
            tsyms = idx.files[tf].parsed.symbols  # type: ignore[union-attr]
            m = _method_of(tsyms, tj, name)
            return ((tf, m), "import") if m is not None else (None, None)
        return None, None  # external library
    local_cls = idx.top_level[fi].get(receiver) if receiver else None
    if local_cls is not None and symbols[local_cls].kind == "class":
        m = _method_of(symbols, local_cls, name)
        if m is not None:
            return (fi, m), "local"
    candidates = idx.methods_by_name.get(name, [])
    if len(candidates) == 1 and len(name) >= 4 and name not in COMMON_METHODS and kind == "call":
        return candidates[0], "unique-method"
    return None, None
