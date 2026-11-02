from fernwood.orders.models import Order, OrderLine
from fernwood.orders.service import calculate_order_total


def test_order_total_includes_tax():
    order = Order(id=None, customer_id=1, region="US-OR", lines=[OrderLine("SKU-1", 2, 1500)])
    assert calculate_order_total(order) == 3000
