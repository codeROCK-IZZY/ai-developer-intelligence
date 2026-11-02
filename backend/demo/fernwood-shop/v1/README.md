# Fernwood Outfitters — online shop

Storefront API (Python/FastAPI), web client (TypeScript/React) and shipping service (Go)
for Fernwood Outfitters, a fictional outdoor-gear retailer.

* `fernwood/` — API: catalogue, checkout, orders, payments, pricing, notifications, reports
* `web/` — storefront web client
* `services/shipping/` — shipping rates and parcel tracking service (Go)

Run the API with `uvicorn fernwood.app:app`; run the back-office report with
`python -m fernwood.cli report 2026-11-01`.
