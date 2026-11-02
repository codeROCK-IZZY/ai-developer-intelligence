"""Orders and order lines."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class OrderStatus(str, Enum):
    PENDING = "pending"
    PAID = "paid"
    SHIPPED = "shipped"
    CANCELLED = "cancelled"


@dataclass
class OrderLine:
    sku: str
    quantity: int
    unit_price_cents: int

    @property
    def subtotal_cents(self) -> int:
        return self.quantity * self.unit_price_cents


@dataclass
class Order:
    id: int | None
    customer_id: int
    lines: list[OrderLine] = field(default_factory=list)
    status: OrderStatus = OrderStatus.PENDING
    region: str = "US-CA"
    currency: str = "USD"
    total_cents: int = 0
    coupon_code: str | None = None
    created: datetime | None = None
