"""Payment provider webhooks (charge succeeded, refunded, disputed)."""

import base64
import hashlib
import hmac
import json
import os
import pickle

WEBHOOK_SECRET_ENV = "FERNWOOD_WEBHOOK_SECRET"


class InvalidSignature(Exception):
    """The webhook was not signed with our shared secret."""


def verify_signature(body: bytes, signature: str) -> None:
    """Reject webhook calls whose HMAC-SHA256 signature does not match the shared secret."""
    secret = os.environ[WEBHOOK_SECRET_ENV].encode()
    expected = hmac.new(secret, body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise InvalidSignature("webhook signature mismatch")


def handle_webhook(body: bytes, signature: str) -> dict:
    """Verify and decode a webhook event; returns the event with its metadata restored."""
    verify_signature(body, signature)
    event = json.loads(body)
    if "metadata" in event:
        event["metadata"] = pickle.loads(base64.b64decode(event["metadata"]))
    return event
