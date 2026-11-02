"""The demo repository: *fernwood-shop*, a fictional online shop (Python API, TypeScript web
client, Go shipping service) with known, hand-labelled answers in ``demo/ground_truth.json``.

Two versions: ``v1`` (full source) and ``v2`` (v1 plus the files in ``demo/fernwood-shop/v2``),
used to demonstrate and evaluate change-impact analysis.

The planted hard-coded secrets are *not* committed: the source holds ``{{DEMO_…}}`` placeholders
and fake but realistic-looking values are generated here, deterministically, at build time — so
secret scanners on this repository stay clean while the analyser still has something to find.
"""

from __future__ import annotations

import json
import random
import string
from functools import cache
from pathlib import Path
from typing import Any

from app.code.archive import build_archive

DEMO_ROOT = Path(__file__).resolve().parents[2] / "demo"
REPO_DIR = DEMO_ROOT / "fernwood-shop"
ARCHIVE_ROOT = "fernwood-shop-main/"  # GitHub-style top-level folder, stripped on import


def _fake(prefix: str, length: int, seed: int) -> str:
    rng = random.Random(seed)
    return prefix + "".join(rng.choice(string.ascii_letters + string.digits) for _ in range(length))


def _placeholders() -> dict[str, str]:
    return {
        "{{DEMO_PAYMENT_KEY}}": _fake("sk_" + "live_", 24, 7),
        "{{DEMO_COUPON_SECRET}}": _fake("cpn_", 28, 11),
    }


def _read(root: Path) -> dict[str, str]:
    files = {}
    for p in sorted(root.rglob("*")):
        if p.is_file():
            files[p.relative_to(root).as_posix()] = p.read_text(encoding="utf-8")
    return files


@cache
def version_files(version: int) -> dict[str, str]:
    """``{path: text}`` of the demo repository at version 1 or 2, secrets filled in."""
    files = _read(REPO_DIR / "v1")
    if version >= 2:
        files.update(_read(REPO_DIR / "v2"))
    subs = _placeholders()
    out = {}
    for path, text in files.items():
        for k, v in subs.items():
            text = text.replace(k, v)
        out[path] = text
    return out


def archive(version: int) -> bytes:
    return build_archive({ARCHIVE_ROOT + p: t for p, t in version_files(version).items()})


@cache
def ground_truth() -> dict[str, Any]:
    data: dict[str, Any] = json.loads((DEMO_ROOT / "ground_truth.json").read_text(encoding="utf-8"))
    return data
