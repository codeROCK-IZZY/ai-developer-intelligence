"""Customer records."""

from dataclasses import dataclass
from datetime import datetime


@dataclass
class Customer:
    id: int
    email: str
    full_name: str
    password_hash: str
    phone: str | None = None
    failed_logins: int = 0
    locked_until: datetime | None = None
    loyalty_points: int = 0
