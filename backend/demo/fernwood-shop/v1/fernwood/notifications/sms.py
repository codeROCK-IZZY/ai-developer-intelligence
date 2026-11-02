"""SMS notifications (shipping updates)."""

import logging

from fernwood.notifications.provider import MessagingClient

logger = logging.getLogger(__name__)


def send_sms(client: MessagingClient, phone: str, text: str) -> bool:
    """Send a text message; returns False (and logs) when the number is invalid."""
    try:
        number = _normalize_number(phone)
    except ValueError:
        logger.warning("invalid phone number, SMS not sent")
        return False
    client.send_message(to=number, body=text[:480])
    return True


def _normalize_number(value: str, country: str = "1") -> str:
    digits = "".join(c for c in value if c.isdigit())
    if value.strip().startswith("+"):
        number = "+" + digits
    elif len(digits) == 10:
        number = "+" + country + digits
    elif len(digits) == 11 and digits.startswith(country):
        number = "+" + digits
    else:
        raise ValueError(f"cannot normalise phone number: {value}")
    if len(number) < 9 or len(number) > 16:
        raise ValueError(f"phone number has an invalid length: {value}")
    return number
