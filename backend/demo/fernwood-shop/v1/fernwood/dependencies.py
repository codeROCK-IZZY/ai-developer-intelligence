"""FastAPI dependency providers."""

from functools import lru_cache

import psycopg

from fernwood.catalog.products import ProductRepository
from fernwood.config import load_settings
from fernwood.customers.repository import CustomerRepository
from fernwood.db import Database
from fernwood.orders.repository import OrderRepository
from fernwood.payments.gateway import PaymentGateway


@lru_cache
def get_database() -> Database:
    return Database(psycopg.connect(load_settings().database_url))


def get_customers() -> CustomerRepository:
    return CustomerRepository(get_database())


def get_orders() -> OrderRepository:
    return OrderRepository(get_database())


def get_products() -> ProductRepository:
    return ProductRepository(get_database())


def get_gateway() -> PaymentGateway:
    return PaymentGateway()
