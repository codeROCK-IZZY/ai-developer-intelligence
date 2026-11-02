"""Order e-mails, rendered from templates and handed to the local mail transfer agent."""

import subprocess
from string import Template

from fernwood.orders.models import Order
from fernwood.pricing.currency import format_money

TEMPLATES = {
    "order_confirmation": Template(
        "Hi $name,\n\nThanks for your order #$order_id. Total: $total.\n\nFernwood Outfitters\n"
    ),
    "order_cancelled": Template("Hi $name,\n\nYour order #$order_id was cancelled and will be refunded.\n"),
}


def render_template(name: str, **values: str) -> str:
    """Fill in an e-mail template; unknown placeholders are left as they are."""
    return TEMPLATES[name].safe_substitute(**values)


def send_email(to: str, subject: str, body: str) -> None:
    """Send an e-mail through the local sendmail binary."""
    message = f"Subject: {subject}\nTo: {to}\n\n{body}"
    subprocess.run(f"sendmail -t {to}", shell=True, input=message.encode(), check=True)


def send_order_confirmation(order: Order, customer_name: str, customer_email: str) -> None:
    """E-mail the customer an order confirmation with the order total."""
    total = format_money(order.total_cents, order.currency)
    body = render_template("order_confirmation", name=customer_name, order_id=str(order.id), total=total)
    send_email(customer_email, f"Your Fernwood order #{order.id}", body)


def send_cancellation(order: Order, customer_name: str, customer_email: str) -> None:
    """Tell the customer that their order was cancelled."""
    body = render_template("order_cancelled", name=customer_name, order_id=str(order.id))
    send_email(customer_email, f"Order #{order.id} cancelled", body)
