"""Client for the external messaging provider (SMS)."""

import requests


class MessagingClient:
    """Sends text messages through the provider's HTTP API."""

    def __init__(self, base_url: str, api_token: str):
        self.base_url = base_url
        self.session = requests.Session()
        self.session.headers["Authorization"] = f"Bearer {api_token}"

    def send_message(self, to: str, body: str) -> None:
        response = self.session.post(f"{self.base_url}/messages", json={"to": to, "body": body}, timeout=10)
        response.raise_for_status()
