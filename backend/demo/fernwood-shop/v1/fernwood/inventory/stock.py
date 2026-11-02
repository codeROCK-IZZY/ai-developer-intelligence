"""Stock levels and reservations, backed by the warehouse system."""

import logging

from fernwood.inventory.warehouse import WarehouseClient
from fernwood.orders import service as order_service

logger = logging.getLogger(__name__)
_warehouse = WarehouseClient()


class OutOfStock(Exception):
    """Not enough units are available to reserve."""


def check_availability(sku: str, quantity: int) -> bool:
    """True when at least ``quantity`` units of ``sku`` can be reserved."""
    return _warehouse.available(sku) >= quantity


def reserve_stock(sku: str, quantity: int) -> None:
    """Reserve units for an order; raises OutOfStock when they are gone."""
    if not _warehouse.reserve(sku, quantity):
        raise OutOfStock(f"{sku}: fewer than {quantity} units left")


def release_stock(sku: str, quantity: int) -> None:
    """Return reserved units, e.g. after a failed payment or a cancellation."""
    try:
        _warehouse.release(sku, quantity)
    except Exception:
        pass


def reserve_order(order) -> None:
    """Reserve every line of an order, translating stock errors into order errors."""
    for line in order.lines:
        try:
            reserve_stock(line.sku, line.quantity)
        except OutOfStock as exc:
            raise order_service.OrderError(str(exc)) from exc
