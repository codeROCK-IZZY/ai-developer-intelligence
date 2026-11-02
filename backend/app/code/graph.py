"""Module dependency graph: import cycles, fan-in/fan-out and centrality.

Nodes are files; an edge ``a → b`` means file ``a`` imports file ``b``.

* **Cycles** — strongly connected components with more than one file (Tarjan's algorithm,
  iterative so large graphs cannot overflow the stack). A cycle means none of its files can be
  understood, tested or extracted without the others.
* **Centrality** — PageRank over the import graph: a file imported by many important files is
  "core". Used to rank the files a newcomer should read first.
"""

from __future__ import annotations

from collections import defaultdict

import numpy as np


def strongly_connected(nodes: list[int], edges: dict[int, set[int]]) -> list[list[int]]:
    index: dict[int, int] = {}
    low: dict[int, int] = {}
    on_stack: set[int] = set()
    stack: list[int] = []
    result: list[list[int]] = []
    counter = 0
    for root in nodes:
        if root in index:
            continue
        work: list[tuple[int, list[int]]] = [(root, sorted(edges.get(root, ())))]
        index[root] = low[root] = counter
        counter += 1
        stack.append(root)
        on_stack.add(root)
        while work:
            v, children = work[-1]
            if children:
                w = children.pop(0)
                if w not in index:
                    index[w] = low[w] = counter
                    counter += 1
                    stack.append(w)
                    on_stack.add(w)
                    work.append((w, sorted(edges.get(w, ()))))
                elif w in on_stack:
                    low[v] = min(low[v], index[w])
                continue
            work.pop()
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[v])
            if low[v] == index[v]:
                comp = []
                while True:
                    w = stack.pop()
                    on_stack.discard(w)
                    comp.append(w)
                    if w == v:
                        break
                result.append(sorted(comp))
    return result


def cycles(nodes: list[int], edges: dict[int, set[int]]) -> list[list[int]]:
    return sorted((c for c in strongly_connected(nodes, edges) if len(c) > 1), key=lambda c: (-len(c), c))


def pagerank(nodes: list[int], edges: dict[int, set[int]], damping: float = 0.85, iters: int = 60) -> dict[int, float]:
    """Power-iteration PageRank where importing a file passes rank *to* it."""
    if not nodes:
        return {}
    pos = {n: i for i, n in enumerate(nodes)}
    n = len(nodes)
    rank = np.full(n, 1.0 / n)
    out_deg = np.array([len(edges.get(v, ())) for v in nodes], dtype=float)
    incoming: dict[int, list[int]] = defaultdict(list)
    for v in nodes:
        for w in edges.get(v, ()):
            if w in pos:
                incoming[pos[w]].append(pos[v])
    for _ in range(iters):
        dangling = rank[out_deg == 0].sum() / n
        new = np.full(n, (1 - damping) / n + damping * dangling)
        for i in range(n):
            src = incoming.get(i)
            if src:
                new[i] += damping * float(sum(rank[j] / out_deg[j] for j in src))
        if np.abs(new - rank).sum() < 1e-9:
            rank = new
            break
        rank = new
    total = rank.sum()
    return {v: float(rank[pos[v]] / total) for v in nodes}


def degrees(nodes: list[int], edges: dict[int, set[int]]) -> tuple[dict[int, int], dict[int, int]]:
    fan_out = {v: len(edges.get(v, ())) for v in nodes}
    fan_in = dict.fromkeys(nodes, 0)
    for v in nodes:
        for w in edges.get(v, ()):
            if w in fan_in:
                fan_in[w] += 1
    return fan_in, fan_out
