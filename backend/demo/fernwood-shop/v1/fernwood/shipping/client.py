"""Client for the internal shipping service (rates)."""

import requests

SHIPPING_URL = "https://shipping.fernwood.internal"


def quote_shipping(weight_grams: int, destination: str) -> int:
    """Ask the shipping service for the price of a parcel, in cents."""
    response = requests.get(
        f"{SHIPPING_URL}/rates", params={"weight": weight_grams, "to": destination}, timeout=10, verify=False
    )
    response.raise_for_status()
    return int(response.json()["rate_cents"])
