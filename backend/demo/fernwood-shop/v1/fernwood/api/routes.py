"""HTTP API for the storefront."""

from fastapi import APIRouter, Depends, Header, HTTPException, Request

from fernwood.auth.service import AuthenticationError, authenticate
from fernwood.auth.tokens import TokenError, decode_token
from fernwood.catalog.search import search_products
from fernwood.dependencies import get_customers, get_gateway, get_orders, get_products
from fernwood.orders.service import OrderError, cancel_order, place_order
from fernwood.payments.webhooks import InvalidSignature, handle_webhook
from fernwood.pricing.tax import tax_rate
from fernwood.shipping.client import quote_shipping
from fernwood.utils.text import slugify, truncate

router = APIRouter(prefix="/api")


def current_customer(authorization: str = Header(...), customers=Depends(get_customers)):
    """Resolve the bearer token to a customer, or fail with 401."""
    try:
        payload = decode_token(authorization.removeprefix("Bearer "))
    except TokenError as exc:
        raise HTTPException(status_code=401, detail="invalid token") from exc
    customer = customers.get(payload["sub"])
    if customer is None:
        raise HTTPException(status_code=401, detail="unknown customer")
    return customer


@router.post("/login")
def login(body: dict, customers=Depends(get_customers)):
    try:
        return {"access_token": authenticate(customers, body["email"], body["password"])}
    except AuthenticationError as exc:
        raise HTTPException(status_code=401, detail="invalid credentials") from exc


@router.get("/products/search")
def product_search(q: str, products=Depends(get_products)):
    return [
        {"sku": p.sku, "name": p.name, "slug": slugify(p.name), "summary": truncate(p.description_html)}
        for p in search_products(products, q)
    ]


@router.post("/orders")
def create_order(
    body: dict,
    customer=Depends(current_customer),
    orders=Depends(get_orders),
    products=Depends(get_products),
    gateway=Depends(get_gateway),
):
    try:
        order = place_order(orders, products, gateway, customer, body["items"], body["region"], body["card_token"])
    except OrderError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"id": order.id, "total_cents": order.total_cents, "status": order.status.value}


@router.post("/orders/{order_id}/cancel")
def cancel(order_id: int, customer=Depends(current_customer), orders=Depends(get_orders), gateway=Depends(get_gateway)):
    try:
        order = cancel_order(orders, gateway, order_id, customer)
    except OrderError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"id": order.id, "status": order.status.value}


@router.get("/shipping/quote")
def shipping_quote(weight_grams: int, region: str):
    return {"region": region, "rate_cents": quote_shipping(weight_grams, region), "tax_rate": tax_rate(region)}


@router.post("/webhooks/payments")
async def payment_webhook(request: Request, signature: str = Header(..., alias="X-Signature")):
    try:
        return handle_webhook(await request.body(), signature)
    except InvalidSignature as exc:
        raise HTTPException(status_code=400, detail="bad signature") from exc
