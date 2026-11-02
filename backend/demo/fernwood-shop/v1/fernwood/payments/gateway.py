"""Card payments through the payment provider's REST API."""

import time

import requests

API_BASE = "https://api.payments.example/v1"
API_KEY = "{{DEMO_PAYMENT_KEY}}"
MAX_ATTEMPTS = 3


class PaymentDeclined(Exception):
    """The card was declined; the customer should use another payment method."""


class PaymentGateway:
    """Charges and refunds cards, retrying transient provider failures with exponential backoff."""

    def __init__(self, session: requests.Session | None = None):
        self.session = session or requests.Session()

    def charge(self, card_token: str, amount_cents: int, currency: str) -> str:
        """Charge a tokenised card and return the provider's charge id."""
        response = self._post("/charges", {"source": card_token, "amount": amount_cents, "currency": currency})
        if response.get("status") == "declined":
            raise PaymentDeclined(response.get("decline_reason", "card declined"))
        return response["id"]

    def refund(self, order_id: int, amount_cents: int) -> str:
        """Refund the charge of an order and return the refund id."""
        response = self._post("/refunds", {"order_id": order_id, "amount": amount_cents})
        return response["id"]

    def _post(self, path: str, payload: dict) -> dict:
        delay = 0.5
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                response = self.session.post(
                    API_BASE + path, json=payload, headers={"Authorization": f"Bearer {API_KEY}"}, timeout=10
                )
                if response.status_code < 500:
                    response.raise_for_status()
                    return response.json()
            except requests.ConnectionError:
                if attempt == MAX_ATTEMPTS:
                    raise
            time.sleep(delay)
            delay *= 2
        raise RuntimeError("payment provider unavailable")
