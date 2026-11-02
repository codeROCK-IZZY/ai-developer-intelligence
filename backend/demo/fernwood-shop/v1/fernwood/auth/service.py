"""Customer login with a temporary lockout after repeated failures."""

from datetime import datetime, timedelta, timezone

from fernwood.auth.passwords import hash_password, needs_rehash, verify_password
from fernwood.auth.tokens import create_access_token
from fernwood.config import load_settings
from fernwood.customers.repository import CustomerRepository

LOCKOUT = timedelta(minutes=15)


class AuthenticationError(Exception):
    """Wrong credentials or a locked account (deliberately indistinguishable)."""


def authenticate(customers: CustomerRepository, email: str, password: str) -> str:
    """Log a customer in and return an access token.

    After too many failed attempts the account is locked for 15 minutes.
    """
    customer = customers.get_by_email(email.strip().lower())
    if customer is None:
        raise AuthenticationError("invalid credentials")
    if customer.locked_until and customer.locked_until > datetime.now(timezone.utc):
        raise AuthenticationError("invalid credentials")
    if not verify_password(password, customer.password_hash):
        _register_failure(customers, customer)
        raise AuthenticationError("invalid credentials")
    if needs_rehash(customer.password_hash):
        customers.update_password_hash(customer.id, hash_password(password))
    _reset_failures(customers, customer)
    return create_access_token(customer.id, scopes=["shop"])


def _register_failure(customers: CustomerRepository, customer) -> None:
    customer.failed_logins += 1
    if customer.failed_logins >= load_settings().max_failed_logins:
        customer.locked_until = datetime.now(timezone.utc) + LOCKOUT
    customers.save(customer)


def _reset_failures(customers: CustomerRepository, customer) -> None:
    if customer.failed_logins or customer.locked_until:
        customer.failed_logins = 0
        customer.locked_until = None
        customers.save(customer)
