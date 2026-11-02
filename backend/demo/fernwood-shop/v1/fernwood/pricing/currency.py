"""Money formatting and currency conversion."""

RATES_TO_USD = {"USD": 1.0, "CAD": 0.73, "EUR": 1.08}
SYMBOLS = {"USD": "$", "CAD": "CA$", "EUR": "€"}


def format_money(amount_cents: int, currency: str = "USD") -> str:
    """'$1,234.50' style formatting for receipts and e-mails."""
    symbol = SYMBOLS.get(currency, currency + " ")
    return f"{symbol}{amount_cents / 100:,.2f}"


def convert(amount_cents: int, from_currency: str, to_currency: str) -> int:
    """Convert between supported currencies using the daily rates table."""
    usd = amount_cents * RATES_TO_USD[from_currency]
    return round(usd / RATES_TO_USD[to_currency])
