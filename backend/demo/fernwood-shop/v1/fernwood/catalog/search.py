"""Relevance-ranked product search for the storefront search box."""

import re

from fernwood.catalog.products import Product, ProductRepository

STOPWORDS = {"the", "a", "an", "for", "and", "with"}


def tokenize(text: str) -> list[str]:
    """Lower-case words without stop words."""
    return [w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in STOPWORDS]


def search_products(products: ProductRepository, query: str, limit: int = 20) -> list[Product]:
    """Rank active products by how many query words appear in the name (weight 3) or description (1)."""
    terms = tokenize(query)
    if not terms:
        return []
    scored = []
    for product in products.all_active():
        name_words = set(tokenize(product.name))
        desc_words = set(tokenize(product.description_html))
        score = sum(3 for t in terms if t in name_words) + sum(1 for t in terms if t in desc_words)
        if score:
            scored.append((score, product))
    scored.sort(key=lambda pair: (-pair[0], pair[1].name))
    return [p for _, p in scored[:limit]]
