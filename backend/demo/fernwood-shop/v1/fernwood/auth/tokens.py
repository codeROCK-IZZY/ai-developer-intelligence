"""Signed access tokens (HMAC-SHA256): stateless and short-lived."""

import base64
import hashlib
import hmac
import json
import time

from fernwood.config import load_settings


class TokenError(Exception):
    """Raised when a token is malformed, forged or expired."""


def create_access_token(customer_id: int, scopes: list[str]) -> str:
    """Issue a signed token for a customer that expires after the configured TTL."""
    settings = load_settings()
    payload = {"sub": customer_id, "scopes": scopes, "exp": int(time.time()) + settings.token_ttl_seconds}
    body = _b64(json.dumps(payload, separators=(",", ":")).encode())
    return f"{body}.{_sign(body, settings.token_secret)}"


def decode_token(token: str) -> dict:
    """Verify the signature and expiry of a token and return its payload."""
    settings = load_settings()
    try:
        body, signature = token.split(".")
    except ValueError as exc:
        raise TokenError("malformed token") from exc
    if not hmac.compare_digest(signature, _sign(body, settings.token_secret)):
        raise TokenError("bad signature")
    payload = json.loads(base64.urlsafe_b64decode(body + "=="))
    if payload["exp"] < time.time():
        raise TokenError("token expired")
    return payload


def _sign(body: str, secret: str) -> str:
    return _b64(hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest())


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")
