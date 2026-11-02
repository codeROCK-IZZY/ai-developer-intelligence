"""Order placement and cancellation."""

from fernwood.inventory import stock
from fernwood.notifications.email import send_cancellation, send_order_confirmation
from fernwood.orders.models import Order, OrderLine, OrderStatus
from fernwood.payments.gateway import PaymentDeclined
from fernwood.pricing.discounts import apply_discounts
from fernwood.pricing.tax import calculate_tax

MAX_LINES = 50
MAX_QUANTITY = 20


class OrderError(Exception):
    """The order cannot be placed or changed as requested."""


def calculate_order_total(order: Order, loyalty_points: int = 0) -> int:
    """Total in cents: line subtotals, minus discounts, plus sales tax for the order's region."""
    subtotal = sum(line.subtotal_cents for line in order.lines)
    discounted = apply_discounts(subtotal, loyalty_points=loyalty_points, line_count=len(order.lines))
    return discounted + calculate_tax(order.region, discounted)


def place_order(orders, products, gateway, customer, items, region, card_token):
    """Validate a cart, reserve stock, charge the card and save the order.

    ``items`` maps SKU to quantity. Stock reservations are released again if the payment fails.
    """
    if not items:
        raise OrderError("the cart is empty")
    if len(items) > MAX_LINES:
        raise OrderError("too many different items")
    order = Order(id=None, customer_id=customer.id, region=region)
    for sku, quantity in items.items():
        if quantity <= 0 or quantity > MAX_QUANTITY:
            raise OrderError(f"invalid quantity for {sku}")
        product = products.get(sku)
        if product is None or not product.active:
            raise OrderError(f"unknown product {sku}")
        if not stock.check_availability(sku, quantity):
            raise OrderError(f"{product.name} is out of stock")
        order.lines.append(OrderLine(sku=sku, quantity=quantity, unit_price_cents=product.price_cents))
    order.total_cents = calculate_order_total(order, loyalty_points=customer.loyalty_points)
    reserved = []
    try:
        for line in order.lines:
            stock.reserve_stock(line.sku, line.quantity)
            reserved.append(line)
        gateway.charge(card_token, order.total_cents, order.currency)
    except (PaymentDeclined, stock.OutOfStock) as exc:
        for line in reserved:
            stock.release_stock(line.sku, line.quantity)
        raise OrderError(str(exc)) from exc
    order.status = OrderStatus.PAID
    orders.save(order)
    if customer.email:
        send_order_confirmation(order, customer.full_name, customer.email)
    return order


def cancel_order(orders, gateway, order_id, customer):
    """Cancel a paid order that has not shipped yet: refund it and put the stock back."""
    order = orders.get(order_id)
    if order is None or order.customer_id != customer.id:
        raise OrderError("order not found")
    if order.status != OrderStatus.PAID:
        raise OrderError("only paid orders that have not shipped can be cancelled")
    gateway.refund(order.id, order.total_cents)
    for line in order.lines:
        stock.release_stock(line.sku, line.quantity)
    order.status = OrderStatus.CANCELLED
    orders.save(order)
    send_cancellation(order, customer.full_name, customer.email)
    return order
