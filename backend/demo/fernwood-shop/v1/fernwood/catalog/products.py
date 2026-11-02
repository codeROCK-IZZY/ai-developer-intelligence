"""Product catalogue."""

from dataclasses import dataclass

from fernwood.db import Database

PRODUCT_COLUMNS = "sku, name, description_html, price_cents, category, weight_grams, active"


@dataclass
class Product:
    sku: str
    name: str
    description_html: str
    price_cents: int
    category: str
    weight_grams: int
    active: bool = True


class ProductRepository:
    """Reads products from the database."""

    def __init__(self, db: Database):
        self.db = db

    def get(self, sku: str) -> Product | None:
        row = self.db.fetch_one("SELECT " + PRODUCT_COLUMNS + " FROM products WHERE sku = %s", (sku,))
        return Product(*row) if row else None

    def list_by_category(self, category: str) -> list[Product]:
        """Active products in a category, for the category landing pages."""
        rows = self.db.execute("SELECT " + PRODUCT_COLUMNS + " FROM products WHERE category = %s AND active", (category,))
        return [Product(*r) for r in rows]

    def all_active(self) -> list[Product]:
        rows = self.db.execute("SELECT " + PRODUCT_COLUMNS + " FROM products WHERE active")
        return [Product(*r) for r in rows]
