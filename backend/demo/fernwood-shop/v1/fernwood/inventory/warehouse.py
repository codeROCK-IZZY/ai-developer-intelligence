"""HTTP client for the warehouse management system."""

import os

import requests


class WarehouseClient:
    """Reads stock levels and manages reservations in the warehouse system."""

    def __init__(self, base_url: str | None = None):
        self.base_url = base_url or os.environ.get("FERNWOOD_WAREHOUSE_URL", "https://warehouse.fernwood.internal")
        self.session = requests.Session()

    def available(self, sku: str) -> int:
        """Units on hand minus units already reserved."""
        response = self.session.get(f"{self.base_url}/stock/{sku}", timeout=5)
        response.raise_for_status()
        return int(response.json()["available"])

    def reserve(self, sku: str, quantity: int) -> bool:
        response = self.session.post(f"{self.base_url}/stock/{sku}/reservations", json={"quantity": quantity}, timeout=5)
        return response.status_code == 201

    def release(self, sku: str, quantity: int) -> None:
        self.session.delete(f"{self.base_url}/stock/{sku}/reservations", json={"quantity": quantity}, timeout=5)
