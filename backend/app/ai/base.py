"""Provider-neutral types for the language-model layer.

The rest of the application talks only to ``LLMProvider``. Two implementations:
``ClaudeProvider`` (Anthropic API, native citations) and ``OfflineProvider`` (deterministic:
points to the most relevant code and quotes its documentation instead of generating text).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

AnswerStatus = Literal["answered", "not_found", "refused", "error"]

NOT_FOUND_TEXT = "I couldn't find code that answers this in the repositories you have access to."


@dataclass(frozen=True)
class Passage:
    """A retrieved code chunk handed to the model. ``index`` is its position in the prompt."""

    index: int
    chunk_id: int
    path: str
    start_line: int
    end_line: int
    symbol: str | None
    symbol_kind: str | None
    signature: str | None
    docstring: str | None
    text: str

    @property
    def title(self) -> str:
        where = f"{self.path} lines {self.start_line}-{self.end_line}"
        return f"{where} ({self.symbol_kind} {self.symbol})" if self.symbol else where


@dataclass
class CitedSentence:
    text: str
    citations: list[tuple[int, str]] = field(default_factory=list)  # (passage index, cited text)


@dataclass
class AnswerResult:
    status: AnswerStatus
    sentences: list[CitedSentence]
    provider: str
    usage: dict[str, Any] = field(default_factory=dict)

    @property
    def text(self) -> str:
        return " ".join(s.text.strip() for s in self.sentences if s.text.strip())


class LLMProvider(Protocol):
    name: str

    def answer(self, question: str, passages: list[Passage]) -> AnswerResult: ...

    def explain(self, code: str, context: str) -> tuple[str, str] | None:
        """Return (explanation, provider) or None when the provider cannot generate one."""
        ...
