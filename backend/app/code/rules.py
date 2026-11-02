"""Deterministic code-quality and security rules.

Every finding comes from a documented rule below: no model is involved, so the same code
always produces the same findings, and each one can be explained. Thresholds follow common
static-analysis defaults (e.g. cyclomatic complexity > 10, the value McCabe proposed).

Findings carry a *fingerprint* that does not include line numbers, so a finding keeps its
identity when unrelated lines move — this is what lets snapshot comparison report findings
as *new* or *fixed* rather than "moved".
"""

from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from dataclasses import dataclass

from app.code.parser import Call, Symbol
from app.code.resolve import FileInput, Resolved, SymRef

SEVERITY_RANK = {"info": 0, "low": 1, "medium": 2, "high": 3}

RULES: dict[str, dict[str, str]] = {
    "hardcoded-secret": {
        "category": "security",
        "title": "Hard-coded secret",
        "fix": "Move the value to a secret store or environment variable and rotate it — it is in the history now.",
    },
    "sql-injection": {
        "category": "security",
        "title": "SQL built from string formatting",
        "fix": "Use parameterised queries (placeholders + parameters); never format values into SQL text.",
    },
    "dangerous-eval": {
        "category": "security",
        "title": "Dynamic code execution",
        "fix": "Avoid eval/exec/new Function; parse the input with a safe, purpose-built parser.",
    },
    "insecure-deserialization": {
        "category": "security",
        "title": "Unsafe deserialisation",
        "fix": "Never unpickle/unsafe-load untrusted data; use JSON or yaml.safe_load.",
    },
    "shell-injection": {
        "category": "security",
        "title": "Command run through a shell",
        "fix": "Pass an argument list without a shell (subprocess.run([...]), execFile) and validate inputs.",
    },
    "tls-verification-disabled": {
        "category": "security",
        "title": "TLS certificate verification disabled",
        "fix": "Keep verification on; trust a private CA bundle explicitly if needed.",
    },
    "weak-hash": {
        "category": "security",
        "title": "Weak hash function",
        "fix": "MD5/SHA-1 are fine for checksums only; use SHA-256, and argon2/bcrypt/scrypt for passwords.",
    },
    "dangerous-html": {
        "category": "security",
        "title": "Raw HTML injection",
        "fix": "Render text, or sanitise the HTML (e.g. DOMPurify) before inserting it.",
    },
    "swallowed-exception": {
        "category": "reliability",
        "title": "Exception silently swallowed",
        "fix": "Handle the error, log it, or let it propagate; catch the narrowest exception type.",
    },
    "high-complexity": {
        "category": "maintainability",
        "title": "High cyclomatic complexity",
        "fix": "Split into smaller functions; replace condition chains with lookup tables or polymorphism.",
    },
    "long-function": {
        "category": "maintainability",
        "title": "Long function",
        "fix": "Extract cohesive steps into well-named helper functions.",
    },
    "deep-nesting": {
        "category": "maintainability",
        "title": "Deeply nested control flow",
        "fix": "Use guard clauses / early returns and extract inner blocks.",
    },
    "too-many-parameters": {
        "category": "maintainability",
        "title": "Too many parameters",
        "fix": "Group related parameters into an object or dataclass.",
    },
    "duplicate-code": {
        "category": "maintainability",
        "title": "Duplicated function",
        "fix": "Keep one implementation and call it from both places.",
    },
    "dead-code": {
        "category": "maintainability",
        "title": "Possibly unused code",
        "fix": "Delete it if it is really unused (it stays in version control history).",
    },
    "import-cycle": {
        "category": "design",
        "title": "Import cycle",
        "fix": "Move the shared code to a lower-level module or invert the dependency.",
    },
    "todo": {"category": "maintainability", "title": "TODO / FIXME marker", "fix": "Track it in the issue tracker."},
}

# ------------------------------------------------------------------ thresholds
COMPLEXITY_MEDIUM = 11
COMPLEXITY_HIGH = 21
LONG_FUNCTION = 60
VERY_LONG_FUNCTION = 120
MAX_NESTING = 4
MAX_PARAMS = 6

SECRET_PATTERNS = [
    ("AWS access key ID", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("private key", re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY")),
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    ("Slack token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
    ("Stripe live key", re.compile(r"\b[sr]k_live_[0-9a-zA-Z]{16,}")),
]
GENERIC_SECRET = re.compile(
    r"""(?i)\b[\w-]*?(password|passwd|pwd|secret|api[_-]?key|access[_-]?token|auth[_-]?token|private[_-]?key)"""
    r"""["']?\s*[:=]\s*["']([^"'\s]{8,})["']"""
)
PLACEHOLDER = re.compile(
    r"(?i)example|change.?me|dummy|placeholder|your[_-]|xxx|\*\*\*|<[^>]*>|\$\{|\{\{|redacted|test"
)
SQL_SINKS = frozenset(
    {
        "execute",
        "executemany",
        "executescript",
        "raw",
        "query",
        "Query",
        "QueryRow",
        "QueryContext",
        "Exec",
        "ExecContext",
        "executeQuery",
        "executeUpdate",
        "prepareStatement",
        "createQuery",
        "rawQuery",
        "text",
    }
)


@dataclass
class Finding:
    rule: str
    severity: str
    file_idx: int
    line: int
    symbol: int | None
    message: str
    evidence: str = ""
    key: str = ""  # stable part of the fingerprint (beyond rule, path and symbol)

    @property
    def category(self) -> str:
        return RULES[self.rule]["category"]


def fingerprint(rule: str, path: str, symbol_qualname: str | None, key: str) -> str:
    raw = f"{rule}|{path}|{symbol_qualname or ''}|{key}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _entropy(s: str) -> float:
    counts = Counter(s)
    return -sum(c / len(s) * math.log2(c / len(s)) for c in counts.values())


def redact(value: str) -> str:
    return value[:4] + "…" + f"({len(value)} chars)" if len(value) > 4 else "…"


def _symbol_at(symbols: list[Symbol], line: int) -> int | None:
    best = None
    for i, s in enumerate(symbols):
        if s.start_line <= line <= s.end_line and (best is None or s.start_line >= symbols[best].start_line):
            best = i
    return best


def secret_findings(fi: int, f: FileInput, is_test: bool) -> list[Finding]:
    out: list[Finding] = []
    symbols = f.parsed.symbols if f.parsed else []
    for lineno, line in enumerate(f.content.splitlines(), start=1):
        if len(line) > 1000:
            continue
        hit: tuple[str, str] | None = None
        for label, pat in SECRET_PATTERNS:
            m = pat.search(line)
            if m:
                hit = (label, m.group(0))
                break
        if hit is None:
            g = GENERIC_SECRET.search(line)
            if g and not PLACEHOLDER.search(g.group(2)) and _entropy(g.group(2)) >= 3.0:
                hit = (f"value assigned to '{g.group(1)}'", g.group(2))
        if hit is not None:
            out.append(
                Finding(
                    "hardcoded-secret",
                    "medium" if is_test else "high",
                    fi,
                    lineno,
                    _symbol_at(symbols, lineno),
                    f"Hard-coded secret: {hit[0]}",
                    evidence=redact(hit[1]),
                    key=hashlib.sha256(hit[1].encode()).hexdigest()[:12],
                )
            )
    return out


def _call_finding(fi: int, c: Call) -> Finding | None:
    name, recv, text = c.name, c.receiver, c.text
    if name in SQL_SINKS and c.dynamic_arg:
        how = {"interpolated": "string interpolation", "concat": "string concatenation", "format": "string formatting"}
        return Finding(
            "sql-injection",
            "high",
            fi,
            c.line,
            c.caller,
            f"Query text passed to {name}() is built with {how[c.dynamic_arg]}",
            text,
            key=name,
        )
    if name in ("eval", "exec") and recv is None:
        return Finding(
            "dangerous-eval", "high", fi, c.line, c.caller, f"{name}() executes dynamic code", text, key=name
        )
    if name == "Function" and c.kind == "new":
        return Finding("dangerous-eval", "high", fi, c.line, c.caller, "new Function() executes dynamic code", text)
    if recv in ("pickle", "cPickle", "dill", "marshal") and name in ("load", "loads"):
        return Finding(
            "insecure-deserialization",
            "high",
            fi,
            c.line,
            c.caller,
            f"{recv}.{name}() can execute code embedded in the data",
            text,
            key=name,
        )
    if recv == "yaml" and name in ("load", "load_all") and "SafeLoader" not in text and "CSafeLoader" not in text:
        return Finding(
            "insecure-deserialization", "high", fi, c.line, c.caller, "yaml.load() without SafeLoader", text, key=name
        )
    if recv == "subprocess" and re.search(r"shell\s*=\s*True", text):
        return Finding(
            "shell-injection", "high", fi, c.line, c.caller, f"subprocess.{name}() runs through a shell", text, key=name
        )
    if recv == "os" and name in ("system", "popen"):
        return Finding(
            "shell-injection", "medium", fi, c.line, c.caller, f"os.{name}() runs a shell command", text, key=name
        )
    if name in ("exec", "execSync") and recv in ("child_process", "cp", "childProcess"):
        return Finding("shell-injection", "medium", fi, c.line, c.caller, f"{recv}.{name}() runs a shell command", text)
    if name == "Command" and recv == "exec" and re.search(r'"(?:ba)?sh"\s*,\s*"-c"', text):
        return Finding(
            "shell-injection", "high", fi, c.line, c.caller, 'exec.Command("sh", "-c", …) runs a shell', text
        )
    if re.search(r"\bverify\s*=\s*False\b|rejectUnauthorized\s*:\s*false|InsecureSkipVerify\s*:\s*true", text):
        return Finding(
            "tls-verification-disabled",
            "medium",
            fi,
            c.line,
            c.caller,
            "TLS certificate verification is disabled",
            text,
        )
    if recv == "hashlib" and name in ("md5", "sha1") and "usedforsecurity=False" not in text:
        return Finding("weak-hash", "low", fi, c.line, c.caller, f"hashlib.{name}() is a weak hash", text, key=name)
    return None


def file_findings(fi: int, f: FileInput, is_test: bool) -> list[Finding]:
    out = secret_findings(fi, f, is_test)
    if f.parsed is None:
        return out
    for si, s in enumerate(f.parsed.symbols):
        if s.kind == "class":
            continue
        where = f"{s.kind} {s.qualified_name}"
        if s.complexity >= COMPLEXITY_MEDIUM:
            sev = "high" if s.complexity >= COMPLEXITY_HIGH else "medium"
            out.append(
                Finding(
                    "high-complexity", sev, fi, s.start_line, si, f"{where} has cyclomatic complexity {s.complexity}"
                )
            )
        if s.loc > LONG_FUNCTION:
            sev = "medium" if s.loc > VERY_LONG_FUNCTION else "low"
            out.append(Finding("long-function", sev, fi, s.start_line, si, f"{where} is {s.loc} lines long"))
        if s.max_nesting > MAX_NESTING:
            out.append(
                Finding(
                    "deep-nesting", "medium", fi, s.start_line, si, f"{where} nests control flow {s.max_nesting} deep"
                )
            )
        if s.params > MAX_PARAMS:
            out.append(
                Finding("too-many-parameters", "low", fi, s.start_line, si, f"{where} takes {s.params} parameters")
            )
    for c in f.parsed.calls:
        found = _call_finding(fi, c)
        if found is not None:
            out.append(found)
    for m in f.parsed.markers:
        if m.kind == "swallowed-exception":
            out.append(
                Finding("swallowed-exception", "low", fi, m.line, m.symbol, "Exception caught and ignored", m.text)
            )
        elif m.kind == "dangerous-html":
            out.append(
                Finding("dangerous-html", "medium", fi, m.line, m.symbol, "HTML inserted without sanitisation", m.text)
            )
        elif m.kind == "todo":
            out.append(Finding("todo", "info", fi, m.line, m.symbol, m.text, key=m.text[:60]))
    return out


ENTRY_NAMES = frozenset(
    {
        "main",
        "init",
        "setup",
        "teardown",
        "setUp",
        "tearDown",
        "constructor",
        "toString",
        "equals",
        "hashCode",
        "String",
        "Error",
        "ServeHTTP",
        "render",
        "handler",
        "lambda_handler",
        "app",
        "create_app",
    }
)


def dead_code_findings(files: list[FileInput], resolved: Resolved, is_test: list[bool]) -> list[Finding]:
    """Functions nothing refers to. Conservative: a symbol is reported only if (1) no call
    resolves to it and (2) its name appears nowhere else in the repository as a word — so
    references through strings, callbacks or re-exports also keep it alive. Decorated
    symbols (routes, fixtures, CLI commands), entry points, dunder methods, exported
    JS/Go/Java symbols and test code are never reported."""
    targets: set[SymRef] = {c.target for c in resolved.calls if c.target is not None}
    words: Counter[str] = Counter()
    for f in files:
        words.update(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", f.content))
    out: list[Finding] = []
    for fi, f in enumerate(files):
        if f.parsed is None or is_test[fi]:
            continue
        for si, s in enumerate(f.parsed.symbols):
            if s.kind == "class" or (fi, si) in targets or s.decorators:
                continue
            if s.name in ENTRY_NAMES or s.name.startswith(("test", "__")) or s.name.startswith("Test"):
                continue
            if f.language == "python":
                parent = f.parsed.symbols[s.parent] if s.parent is not None else None
                if parent is not None and parent.kind == "class" and not s.name.startswith("_"):
                    continue  # public methods may be called by frameworks or via duck typing
                if parent is not None and parent.kind != "class":
                    continue  # nested helper
            elif s.exported:
                continue
            if words[s.name] > 1:
                continue
            out.append(
                Finding("dead-code", "low", fi, s.start_line, si, f"{s.kind} {s.qualified_name} is never referenced")
            )
    return out
