"""Input validation for customer data."""

import re

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[a-z]{2,}$", re.IGNORECASE)


def validate_email(email: str) -> str:
    """Return the normalised e-mail address or raise ValueError."""
    email = email.strip().lower()
    if not EMAIL_RE.match(email):
        raise ValueError(f"invalid e-mail address: {email}")
    return email


def normalize_phone(raw: str, default_country: str = "1") -> str:
    """Normalise a phone number to E.164 format (+<country><number>)."""
    digits = "".join(ch for ch in raw if ch.isdigit())
    if raw.strip().startswith("+"):
        result = "+" + digits
    elif len(digits) == 10:
        result = "+" + default_country + digits
    elif len(digits) == 11 and digits.startswith(default_country):
        result = "+" + digits
    else:
        raise ValueError(f"cannot normalise phone number: {raw}")
    if len(result) < 9 or len(result) > 16:
        raise ValueError(f"phone number has an invalid length: {raw}")
    return result
