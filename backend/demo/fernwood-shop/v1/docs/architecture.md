# Architecture

The storefront API owns customers, the catalogue and orders. Placing an order validates the cart,
reserves stock in the warehouse system, charges the card through the payment provider and sends a
confirmation e-mail. Payment provider webhooks report refunds and disputes.

Prices are calculated at checkout: line subtotals, then discounts (bulk, seasonal, loyalty points),
then sales tax for the shipping region. The shipping service prices parcels by weight, zone and speed.
