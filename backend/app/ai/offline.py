"""Offline, deterministic answer provider (no language model).

It does not write prose. It answers "where/how is X done?" by pointing at the most relevant
code — symbol, file and lines — and quoting the symbol's own documentation or signature, each
cited. The UI labels these answers as offline so they are never mistaken for generated text.
"""

from __future__ import annotations

import re

from app.ai.base import AnswerResult, CitedSentence, Passage

MAX_LOCATIONS = 3


def _first_sentence(doc: str) -> str:
    doc = doc.strip()
    # a sentence ends at . ! ? followed by an upper-case letter or the end ("e.g. after" does not end one)
    m = re.match(r"(.+?[.!?])(?=\s+[A-Z`\"'(]|\s*$)", doc, re.DOTALL)
    return (m.group(1) if m else doc)[:240]


class OfflineProvider:
    name = "offline"

    def answer(self, question: str, passages: list[Passage]) -> AnswerResult:
        sentences: list[CitedSentence] = []
        seen: set[str] = set()
        for p in passages:
            key = p.symbol or f"{p.path}:{p.start_line}"
            if key in seen:
                continue
            seen.add(key)
            where = f"{p.path} (lines {p.start_line}–{p.end_line})"
            if p.symbol:
                lead = "Most relevant" if not sentences else "Also relevant"
                text = f"{lead}: {p.symbol_kind} `{p.symbol}` in {where}"
                if p.docstring:
                    text += f" — {_first_sentence(p.docstring)}"
                    cited = p.docstring
                else:
                    cited = p.signature or p.text.splitlines()[0]
            else:
                text = f"{'Most relevant' if not sentences else 'Also relevant'}: {where}"
                cited = next((line for line in p.text.splitlines() if line.strip()), p.text[:200])
            sentences.append(CitedSentence(text=text.rstrip(".") + ".", citations=[(p.index, cited.strip()[:300])]))
            if len(sentences) == MAX_LOCATIONS:
                break
        if not sentences:
            return AnswerResult(status="not_found", sentences=[], provider=self.name)
        return AnswerResult(status="answered", sentences=sentences, provider=self.name)

    def explain(self, code: str, context: str) -> tuple[str, str] | None:
        return None
