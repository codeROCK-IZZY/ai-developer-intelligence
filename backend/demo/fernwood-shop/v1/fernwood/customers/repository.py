"""Persistence for customers."""

from fernwood.customers.models import Customer
from fernwood.db import Database

CUSTOMER_COLUMNS = "id, email, full_name, password_hash, phone, failed_logins, locked_until, loyalty_points"


class CustomerRepository:
    """Loads and saves customers using parameterised SQL."""

    def __init__(self, db: Database):
        self.db = db

    def get(self, customer_id: int) -> Customer | None:
        return self._one("id", customer_id)

    def get_by_email(self, email: str) -> Customer | None:
        return self._one("email", email)

    def save(self, customer: Customer) -> None:
        self.db.execute(
            "UPDATE customers SET failed_logins = %s, locked_until = %s, loyalty_points = %s WHERE id = %s",
            (customer.failed_logins, customer.locked_until, customer.loyalty_points, customer.id),
        )

    def update_password_hash(self, customer_id: int, password_hash: str) -> None:
        self.db.execute("UPDATE customers SET password_hash = %s WHERE id = %s", (password_hash, customer_id))

    def _one(self, column: str, value: object) -> Customer | None:
        if column not in ("id", "email"):
            raise ValueError(f"cannot look customers up by {column}")
        row = self.db.fetch_one("SELECT " + CUSTOMER_COLUMNS + " FROM customers WHERE " + column + " = %s", (value,))
        return Customer(*row) if row else None
