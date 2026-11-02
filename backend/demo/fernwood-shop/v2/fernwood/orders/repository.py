"""Persistence for orders."""

from fernwood.db import Database
from fernwood.orders.models import Order, OrderLine, OrderStatus


class OrderRepository:
    """Loads and saves orders with their lines."""

    def __init__(self, db: Database):
        self.db = db

    def save(self, order: Order) -> Order:
        """Insert a new order with its lines, or update the status of an existing one."""
        with self.db.transaction():
            if order.id is None:
                row = self.db.fetch_one(
                    "INSERT INTO orders (customer_id, status, region, currency, total_cents) "
                    "VALUES (%s, %s, %s, %s, %s) RETURNING id",
                    (order.customer_id, order.status.value, order.region, order.currency, order.total_cents),
                )
                order.id = row[0]
                for line in order.lines:
                    self.db.execute(
                        "INSERT INTO order_lines (order_id, sku, quantity, unit_price_cents) VALUES (%s, %s, %s, %s)",
                        (order.id, line.sku, line.quantity, line.unit_price_cents),
                    )
            else:
                self.db.execute("UPDATE orders SET status = %s WHERE id = %s", (order.status.value, order.id))
        return order

    def get(self, order_id: int) -> Order | None:
        row = self.db.fetch_one(
            "SELECT id, customer_id, status, region, currency, total_cents, created FROM orders WHERE id = %s",
            (order_id,),
        )
        if row is None:
            return None
        line_rows = self.db.execute(
            "SELECT sku, quantity, unit_price_cents FROM order_lines WHERE order_id = %s", (order_id,)
        )
        return Order(
            id=row[0], customer_id=row[1], lines=[OrderLine(*r) for r in line_rows], status=OrderStatus(row[2]),
            region=row[3], currency=row[4], total_cents=row[5], created=row[6],
        )

    def list_for_customer(self, customer_id: int) -> list[Order]:
        ids = self.db.execute("SELECT id FROM orders WHERE customer_id = %s ORDER BY id DESC", (customer_id,))
        return [order for (order_id,) in ids if (order := self.get(order_id)) is not None]

    def list_for_day(self, day) -> list[Order]:
        """All orders created on a calendar day (for the sales report)."""
        ids = self.db.execute("SELECT id FROM orders WHERE created::date = %s ORDER BY id", (day,))
        return [order for (order_id,) in ids if (order := self.get(order_id)) is not None]

    def search_orders(self, status: str, region: str) -> list[int]:
        """Find order ids by status and region (used by the back-office order list)."""
        rows = self.db.execute("SELECT id FROM orders WHERE status = %s AND region = %s ORDER BY id", (status, region))
        return [r[0] for r in rows]
