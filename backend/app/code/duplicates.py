"""Near-duplicate functions (copy-paste, including renamed variables).

Each function body becomes a sequence of *normalised* tokens (identifiers → ``$``, literals →
``#``), so a copy whose variables were renamed still matches. Sets of 6-token shingles are
compared with MinHash (128 permutations) and candidate pairs come from locality-sensitive
hashing (32 bands × 4 rows), which avoids comparing every pair of functions. Pairs whose
estimated Jaccard similarity is ≥ ``threshold`` are reported, grouped into clusters.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict

import numpy as np

NUM_PERM = 128
BANDS = 32
ROWS = NUM_PERM // BANDS
SHINGLE = 6
MIN_TOKENS = 40
_MASK = (1 << 64) - 1
_PERM = np.arange(NUM_PERM, dtype=np.uint64)


def signature(tokens: list[str]) -> np.ndarray:
    sig = np.full(NUM_PERM, _MASK, dtype=np.uint64)
    shingles = {" ".join(tokens[i : i + SHINGLE]) for i in range(max(1, len(tokens) - SHINGLE + 1))}
    for sh in shingles:
        base = hashlib.blake2b(sh.encode(), digest_size=16).digest()
        a = np.uint64(int.from_bytes(base[:8], "little"))
        b = np.uint64(int.from_bytes(base[8:], "little") | 1)
        np.minimum(sig, a + _PERM * b, out=sig)  # uint64 arithmetic wraps mod 2**64, as intended
    return sig


def find_duplicates(items: list[tuple[int, list[str]]], threshold: float = 0.8) -> list[tuple[int, int, float]]:
    """``items`` = (key, normalised tokens). Returns (key_a, key_b, similarity) with key_a < key_b."""
    sigs: dict[int, np.ndarray] = {k: signature(toks) for k, toks in items if len(toks) >= MIN_TOKENS}
    buckets: dict[tuple[int, bytes], list[int]] = defaultdict(list)
    for k, sig in sigs.items():
        for band in range(BANDS):
            buckets[(band, sig[band * ROWS : (band + 1) * ROWS].tobytes())].append(k)
    candidates: set[tuple[int, int]] = set()
    for keys in buckets.values():
        if 1 < len(keys) <= 50:
            for i, a in enumerate(keys):
                for b in keys[i + 1 :]:
                    candidates.add((min(a, b), max(a, b)))
    pairs = []
    for a, b in sorted(candidates):
        sim = float(np.mean(sigs[a] == sigs[b]))
        if sim >= threshold:
            pairs.append((a, b, round(sim, 3)))
    return pairs


def clusters(pairs: list[tuple[int, int, float]]) -> list[list[int]]:
    parent: dict[int, int] = {}

    def find(x: int) -> int:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b, _ in pairs:
        parent[find(a)] = find(b)
    groups: dict[int, list[int]] = defaultdict(list)
    for x in list(parent):
        groups[find(x)].append(x)
    return sorted((sorted(g) for g in groups.values()), key=lambda g: g[0])
