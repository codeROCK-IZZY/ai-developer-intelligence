"""Password hashing with PBKDF2-HMAC-SHA256 and a per-password random salt."""

import hashlib
import hmac
import secrets

ITERATIONS = 310_000
SALT_BYTES = 16


def hash_password(password: str) -> str:
    """Hash a password for storage. Returns 'pbkdf2$iterations$salt$hash'."""
    salt = secrets.token_hex(SALT_BYTES)
    digest = _derive(password, salt, ITERATIONS)
    return f"pbkdf2${ITERATIONS}${salt}${digest}"


def verify_password(password: str, stored: str) -> bool:
    """Check a password against a stored hash in constant time."""
    try:
        scheme, iterations, salt, expected = stored.split("$")
    except ValueError:
        return False
    if scheme != "pbkdf2":
        return False
    actual = _derive(password, salt, int(iterations))
    return hmac.compare_digest(actual, expected)


def needs_rehash(stored: str) -> bool:
    """True when the hash was created with fewer iterations than today's setting."""
    parts = stored.split("$")
    return len(parts) != 4 or int(parts[1]) < ITERATIONS


def _derive(password: str, salt: str, iterations: int) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), iterations).hex()
