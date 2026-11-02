"""Text helpers for URLs and display."""

import re
import unicodedata


def slugify(value: str) -> str:
    """'Trail Runner 2 (Men's)' -> 'trail-runner-2-mens'."""
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    value = re.sub(r"[^\w\s-]", "", value).strip().lower()
    return re.sub(r"[-\s]+", "-", value)


def truncate(text: str, length: int = 80) -> str:
    """Shorten text to ``length`` characters, ending with an ellipsis."""
    return text if len(text) <= length else text[: length - 1].rstrip() + "…"


def _strip_accents_old(value: str) -> str:
    # Superseded by unicodedata in slugify().
    table = str.maketrans("áéíóúñ", "aeioun")
    return value.translate(table)
