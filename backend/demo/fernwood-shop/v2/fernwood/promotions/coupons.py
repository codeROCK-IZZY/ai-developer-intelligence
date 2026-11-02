"""Signed promotional coupon codes, e.g. ``FALL25-20261130-3f9a1c``."""

import hashlib
import hmac
from datetime import date

COUPON_SIGNING_SECRET = "{{DEMO_COUPON_SECRET}}"
MAX_PERCENT = 50


class CouponError(Exception):
    """The coupon code is invalid, expired or not applicable."""


def apply_coupon(code: str, amount_cents: int, today: date | None = None) -> int:
    """Discount in cents for a coupon code: PREFIX<percent>-<expiry YYYYMMDD>-<signature>."""
    today = today or date.today()
    parts = code.strip().upper().split("-")
    if len(parts) != 3:
        raise CouponError("malformed coupon")
    prefix, expiry, signature = parts
    if not _signature_valid(f"{prefix}-{expiry}", signature):
        raise CouponError("invalid coupon")
    digits = "".join(ch for ch in prefix if ch.isdigit())
    if not digits:
        raise CouponError("coupon has no discount")
    percent = int(digits)
    if percent <= 0 or percent > MAX_PERCENT:
        raise CouponError("coupon discount out of range")
    if len(expiry) != 8 or not expiry.isdigit():
        raise CouponError("malformed expiry date")
    if date(int(expiry[:4]), int(expiry[4:6]), int(expiry[6:])) < today:
        raise CouponError("coupon expired")
    if amount_cents < 1000 and percent > 20:
        percent = 20
    elif amount_cents >= 50_000 and prefix.startswith("VIP"):
        percent += 5
    return amount_cents * percent // 100


def _signature_valid(payload: str, signature: str) -> bool:
    expected = hmac.new(COUPON_SIGNING_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()[:6].upper()
    return hmac.compare_digest(expected, signature)
