"""Discount rules applied at checkout."""

from datetime import date

LOYALTY_POINT_VALUE_CENTS = 1
MAX_LOYALTY_SHARE = 0.2
BULK_THRESHOLD = 5
BULK_DISCOUNT = 0.05


def apply_discounts(subtotal_cents: int, loyalty_points: int = 0, line_count: int = 1) -> int:
    """Apply the bulk, seasonal and loyalty discounts (in that order) and return the new subtotal."""
    amount = subtotal_cents
    if line_count >= BULK_THRESHOLD:
        amount -= int(amount * BULK_DISCOUNT)
    amount -= seasonal_discount(amount, date.today())
    amount -= loyalty_discount(amount, loyalty_points)
    return max(amount, 0)


def loyalty_discount(amount_cents: int, points: int) -> int:
    """Each loyalty point is worth one cent, capped at 20% of the amount."""
    return min(points * LOYALTY_POINT_VALUE_CENTS, int(amount_cents * MAX_LOYALTY_SHARE))


def seasonal_discount(amount_cents: int, today: date) -> int:
    """10% off during the November sale."""
    return int(amount_cents * 0.10) if today.month == 11 else 0
