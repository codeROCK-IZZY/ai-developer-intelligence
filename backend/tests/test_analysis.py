"""Unit tests for the analysis engine (no database): parsing, resolution, graph, rules,
duplicates, chunking, archives — and a regression guard on the labelled demo repository."""

import io
import zipfile

import pytest

from app.code import duplicates, graph
from app.code.analyze import analyze, grade
from app.code.archive import SourceFile, build_archive, read_archive
from app.code.chunking import chunk_file, expand_identifiers, split_identifier
from app.code.languages import detect_language, is_test_path
from app.code.parser import parse_source
from app.code.rules import fingerprint, redact
from app.core.errors import UnprocessableData
from app.evaluation.structure import evaluate_call_graph, evaluate_cycles, evaluate_findings
from app.synthetic import demo


def sources(files: dict[str, str]) -> list[SourceFile]:
    return read_archive(build_archive(files), max_entries=1000, max_total_mb=10, max_file_kb=512, max_files=1000).files


# ---------------------------------------------------------------- parser
PY = '''
import os
from ..models import User as U
from app.auth import passwords

class Gateway:
    """Talks to the payment provider."""
    def charge(self, amount, retries=3):
        for i in range(retries):
            if amount > 0 and i < 2:
                return self._post(amount)
        try:
            pass
        except Exception:
            pass
        # TODO: handle refunds
        db.execute(f"select {amount}")
        return passwords.hash_password("x")

@router.get("/x")
def handler(a):
    return [i for i in a if i]

if __name__ == "__main__":
    handler(1)
'''


def test_python_symbols_metrics_and_markers():
    p = parse_source("python", PY)
    by_name = {s.qualified_name: s for s in p.symbols}
    assert set(by_name) == {"Gateway", "Gateway.charge", "handler"}
    charge = by_name["Gateway.charge"]
    assert charge.kind == "method" and charge.params == 2  # self excluded
    assert charge.complexity == 5  # for, if, and, except + 1
    assert charge.max_nesting == 2
    assert by_name["Gateway"].docstring == "Talks to the payment provider."
    assert by_name["handler"].decorators == ['router.get("/x")']
    assert by_name["handler"].complexity == 3  # comprehension for + if
    kinds = {m.kind for m in p.markers}
    assert kinds == {"swallowed-exception", "todo", "entry-point"}
    execute = next(c for c in p.calls if c.name == "execute")
    assert execute.receiver == "db" and execute.dynamic_arg == "interpolated"
    rel = next(i for i in p.imports if i.level)
    assert rel.level == 2 and rel.module == "models" and rel.names == [("User", "U")]


def test_typescript_symbols_exports_and_jsx_marker():
    src = (
        'import { a, b as c } from "./x";\nimport * as api from "../api";\n'
        "/** Adds things. */\nexport async function f(x: number): Promise<void> { if (a && b) { api.get(`/x/${x}`); } }\n"
        "export const g = (y) => y ?? 1;\nexport class Cart { total(): number { return this.sum(); } }\n"
        "const el = () => <div dangerouslySetInnerHTML={{__html: x}} />;\n"
    )
    p = parse_source("tsx", src)
    by_name = {s.qualified_name: s for s in p.symbols}
    assert by_name["f"].exported and by_name["f"].docstring == "Adds things."
    assert by_name["f"].signature == "async function f(x: number): Promise<void>"
    assert by_name["f"].complexity == 3
    assert by_name["g"].signature == "g = (y) =>"
    assert by_name["Cart.total"].kind == "method" and by_name["Cart.total"].exported
    assert not by_name["el"].exported
    assert any(m.kind == "dangerous-html" for m in p.markers)
    assert next(i for i in p.imports if i.module == "../api").alias == "api"


def test_go_and_java():
    go = parse_source(
        "go",
        'package rates\nimport (\n "fmt"\n r "github.com/x/y/zones"\n)\n// S is a thing.\ntype S struct{}\n'
        "func (s *S) M(x int) int { if x > 1 && x < 3 { return r.Calc(x) }; return 0 }\n"
        'func helper() { db.Query(fmt.Sprintf("select %s", x)) }\n',
    )
    names = {s.qualified_name: s for s in go.symbols}
    assert go.package == "rates" and names["S"].docstring == "S is a thing."
    assert names["S.M"].complexity == 3 and names["S.M"].exported and not names["helper"].exported
    assert next(c for c in go.calls if c.name == "Query").dynamic_arg == "format"
    java = parse_source(
        "java",
        "package com.acme;\nimport com.acme.Foo;\n/** Class A. */\n"
        "public class A { private int m(int x) { if (x>1 && x<5) { return foo.bar(x); } return new Foo().y(); } }\n",
    )
    jn = {s.qualified_name: s for s in java.symbols}
    assert jn["A"].exported and jn["A"].docstring == "Class A." and not jn["A.m"].exported
    assert jn["A.m"].complexity == 3
    assert any(c.kind == "new" and c.name == "Foo" for c in java.calls)


def test_constant_concatenation_is_not_dynamic_sql():
    p = parse_source(
        "python",
        'def a(db, x):\n    db.execute("SELECT " + COLUMNS + " FROM t")\n    db.execute("SELECT * FROM t WHERE id=" + x)\n',
    )
    flags = [c.dynamic_arg for c in p.calls if c.name == "execute"]
    assert flags == [None, "concat"]


def test_language_detection_and_test_paths():
    assert detect_language("src/app.tsx") == "tsx"
    assert detect_language("README.md") == "text"
    assert detect_language("Dockerfile") == "text"
    assert detect_language("logo.png") is None
    assert is_test_path("tests/test_x.py") and is_test_path("web/src/cart.test.ts") and is_test_path("pkg/x_test.go")
    assert not is_test_path("app/testing_utils_notatest.py".replace("testing_utils_notatest", "contest"))


# ---------------------------------------------------------------- resolution + graph
REPO = {
    "pkg/__init__.py": "",
    "pkg/a.py": "from pkg.b import helper\nfrom . import c\n\n\ndef run():\n    helper()\n    c.util()\n    return Box().open_box()\n\n\nclass Box:\n    def open_box(self):\n        return self._inner()\n\n    def _inner(self):\n        return 1\n",
    "pkg/b.py": "import pkg.a\n\n\ndef helper():\n    return 2\n",
    "pkg/c.py": "def util():\n    return 3\n",
    "web/main.ts": 'import { fmt } from "./util";\nimport * as api from "./api";\nexport function go() { fmt(); api.call(); }\n',
    "web/util.ts": "export function fmt() { return 1; }\n",
    "web/api/index.ts": "export function call() { return 2; }\n",
    "svc/go.mod": "module example.com/svc\n",
    "svc/main.go": 'package main\nimport "example.com/svc/calc"\nfunc main() { calc.Add(1) }\n',
    "svc/calc/calc.go": "package calc\nfunc Add(x int) int { return x }\n",
}


def test_resolution_across_languages():
    a = analyze(sources(REPO))
    edges = set()
    for cr in a.resolved.calls:
        if cr.target is None:
            continue
        call = a.files[cr.file_idx].parsed.calls[cr.call_idx]
        caller = a.files[cr.file_idx].parsed.symbols[call.caller].qualified_name if call.caller is not None else "<mod>"
        callee = a.files[cr.target[0]]
        edges.add((caller, f"{callee.path}::{callee.parsed.symbols[cr.target[1]].qualified_name}", cr.resolution))
    assert ("run", "pkg/b.py::helper", "import") in edges
    assert ("run", "pkg/c.py::util", "import") in edges  # from . import c → c.util()
    assert ("run", "pkg/a.py::Box", "local") in edges
    assert ("Box.open_box", "pkg/a.py::Box._inner", "self") in edges
    assert ("go", "web/util.ts::fmt", "import") in edges
    assert ("go", "web/api/index.ts::call", "import") in edges  # directory index resolution
    assert ("main", "svc/calc/calc.go::Add", "import") in edges  # Go module path → package dir
    assert [sorted(a.files[i].path for i in c) for c in a.cycles] == [["pkg/a.py", "pkg/b.py"]]


def test_graph_algorithms():
    edges = {1: {2}, 2: {3}, 3: {1}, 4: {1}}
    assert graph.cycles([1, 2, 3, 4], edges) == [[1, 2, 3]]
    pr = graph.pagerank([1, 2, 3, 4, 5], {1: {5}, 2: {5}, 3: {5}, 4: {5}})
    assert max(pr, key=pr.get) == 5 and abs(sum(pr.values()) - 1) < 1e-9
    fan_in, fan_out = graph.degrees([1, 2, 3, 4], edges)
    assert fan_in[1] == 2 and fan_out[4] == 1


def test_duplicates_detect_renamed_copies_only():
    body = "def f(a, b):\n    total = 0\n    for x in a:\n        if x > b:\n            total += x * 2\n        else:\n            total -= x\n    return total + len(a) - b\n"
    renamed = body.replace("total", "acc").replace("def f", "def g")
    other = "def h(s):\n    parts = s.split(',')\n    return {p.strip(): len(p) for p in parts if p}\n"
    p = parse_source("python", body + "\n\n" + renamed.replace("def g", "def g", 1) + "\n\n" + other)
    items = [(i, s.tokens) for i, s in enumerate(p.symbols)]
    pairs = duplicates.find_duplicates(items, threshold=0.8)
    assert [(a, b) for a, b, _ in pairs] == [(0, 1)]
    assert duplicates.clusters(pairs) == [[0, 1]]


# ---------------------------------------------------------------- rules
def _rules(files: dict[str, str]) -> set[tuple[str, str]]:
    a = analyze(sources(files))
    return {(f.rule, a.files[f.file_idx].path) for f in a.findings}


def test_security_rules():
    code = (
        "import pickle, subprocess, yaml, requests, hashlib\n\n\n"
        "def risky(db, data, cmd, url, expr):\n"
        "    db.execute('SELECT * FROM t WHERE x = %s' % data)\n"
        "    pickle.loads(data)\n"
        "    yaml.load(data)\n"
        "    subprocess.run(cmd, shell=True)\n"
        "    requests.get(url, verify=False)\n"
        "    hashlib.md5(data)\n"
        "    return eval(expr)\n\n\n"
        "def safe(db, data):\n"
        "    db.execute('SELECT * FROM t WHERE x = %s', (data,))\n"
        "    yaml.load(data, Loader=yaml.SafeLoader)\n"
        "    return hashlib.sha256(data)\n"
    )
    found = {r for r, _ in _rules({"app/x.py": code})}
    assert {
        "sql-injection",
        "insecure-deserialization",
        "shell-injection",
        "tls-verification-disabled",
        "weak-hash",
        "dangerous-eval",
    } <= found
    a = analyze(sources({"app/x.py": code}))
    sql = [f for f in a.findings if f.rule == "sql-injection"]
    deser = [f for f in a.findings if f.rule == "insecure-deserialization"]
    assert len(sql) == 1 and len(deser) == 2  # pickle + yaml in risky(); the safe() variants are not flagged
    assert all(f.line < 13 for f in sql + deser)


def test_secret_detection_redacts_and_skips_placeholders():
    key = "AKIA" + "Z" * 4 + "Q7P2" + "M4XK" + "8RTW"
    files = {
        "app/settings.py": f'AWS_KEY = "{key}"\nDB_PASSWORD = "changeme-in-production"\nAPI_KEY = "${{API_KEY}}"\n',
        "tests/test_x.py": 'SERVICE_PASSWORD = "Xk29!qPz7Lm4Rt"\n',
    }
    a = analyze(sources(files))
    secrets = [f for f in a.findings if f.rule == "hardcoded-secret"]
    assert {(a.files[f.file_idx].path, f.severity) for f in secrets} == {
        ("app/settings.py", "high"),
        ("tests/test_x.py", "medium"),  # secrets in tests matter less, but still matter
    }
    assert all(key not in f.evidence for f in secrets)
    assert redact("abcdefgh") == "abcd…(8 chars)"


def test_dead_code_is_conservative():
    files = {
        "app/m.py": (
            "def used():\n    return 1\n\n\ndef unused_helper():\n    return 2\n\n\n"
            "def by_name():\n    return 3\n\n\nHANDLERS = {'x': by_name}\n\n\n"
            "@app.get('/')\ndef route():\n    return used()\n\n\nclass K:\n    def public(self):\n        return 1\n\n"
            "    def _private(self):\n        return 2\n"
        ),
        "web/x.ts": "export function exportedButUnused() { return 1; }\nfunction localUnused() { return 2; }\n",
    }
    a = analyze(sources(files))
    dead = {a.files[f.file_idx].parsed.symbols[f.symbol].qualified_name for f in a.findings if f.rule == "dead-code"}
    assert dead == {"unused_helper", "K._private", "localUnused"}


def test_fingerprint_ignores_line_numbers():
    assert fingerprint("dead-code", "a.py", "f", "") == fingerprint("dead-code", "a.py", "f", "")
    assert fingerprint("dead-code", "a.py", "f", "") != fingerprint("dead-code", "a.py", "g", "")


def test_health_grades():
    assert [grade(x) for x in (95, 80, 65, 45, 10)] == ["A", "B", "C", "D", "E"]


# ---------------------------------------------------------------- chunking
def test_identifier_splitting_and_expansion():
    assert split_identifier("getHTTPResponse_code") == ["get", "http", "response", "code"]
    assert "calculate order total" in expand_identifiers("x = calculate_order_total(o)")
    assert "total cents" in expand_identifiers("cart.totalCents()")


def test_chunks_follow_symbols_and_split_long_functions():
    long_body = "\n".join(f"    x{i} = {i}" for i in range(150))
    src = f'"""Module."""\nimport os\nLIMIT = 3\nMODE = "x"\n\n\ndef short():\n    return 1\n\n\ndef long():\n{long_body}\n    return x0\n'
    p = parse_source("python", src)
    chunks = chunk_file("python", src, p)
    kinds = [c.kind for c in chunks]
    assert kinds.count("module") == 1 and kinds.count("function") >= 4
    windows = [c for c in chunks if c.symbol == 1]
    assert len(windows) >= 3 and all(c.end_line - c.start_line < 60 for c in windows)
    assert windows[1].text.startswith("def long()")  # every window carries the signature


# ---------------------------------------------------------------- archives
def _zip(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for k, v in entries.items():
            z.writestr(k, v)
    return buf.getvalue()


def test_archive_strips_root_and_skips_noise():
    data = _zip(
        {
            "repo-main/app/x.py": b"def f():\n    return 1\n",
            "repo-main/node_modules/lib/index.js": b"x",
            "repo-main/.git/config": b"x",
            "repo-main/logo.png": b"\x89PNG",
            "repo-main/static/app.min.js": b"var a=1",
            "repo-main/data/blob.txt": b"\x00\x01binary",
        }
    )
    c = read_archive(data, max_entries=100, max_total_mb=10, max_file_kb=512, max_files=100)
    assert [f.path for f in c.files] == ["app/x.py"]
    assert sum(c.skipped.values()) == 5


def test_archive_rejects_traversal_bombs_and_garbage():
    with pytest.raises(UnprocessableData):
        read_archive(_zip({"../evil.py": b"x"}), max_entries=10, max_total_mb=1, max_file_kb=10, max_files=10)
    with pytest.raises(UnprocessableData):
        read_archive(
            _zip({f"f{i}.py": b"x" for i in range(20)}), max_entries=10, max_total_mb=1, max_file_kb=10, max_files=10
        )
    with pytest.raises(UnprocessableData):
        read_archive(_zip({"a.py": b"x" * 2_000_000}), max_entries=10, max_total_mb=1, max_file_kb=10, max_files=10)
    with pytest.raises(UnprocessableData):
        read_archive(b"not a zip", max_entries=10, max_total_mb=1, max_file_kb=10, max_files=10)


# ---------------------------------------------------------------- labelled demo (regression guard)
def test_demo_repository_analysis_matches_labels():
    """The analyser against the hand-labelled demo repository. Thresholds sit just below the
    current scores so that a regression fails CI; the full numbers are in eval/reports."""
    a = analyze(sources(demo.version_files(1)))
    truth = demo.ground_truth()
    findings = evaluate_findings(a, truth)["overall"]
    calls = evaluate_call_graph(a, truth)["overall"]
    assert findings["recall"] == 1.0 and findings["precision"] >= 0.9
    assert calls["precision"] >= 0.95 and calls["recall"] >= 0.85
    assert evaluate_cycles(a, truth)["recall"] == 1.0
    assert "{{DEMO_" not in "".join(demo.version_files(1).values())  # secrets are filled in at build time
