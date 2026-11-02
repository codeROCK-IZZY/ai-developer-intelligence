"""Sales tax by shipping region."""

TAX_RATES = {
    "US-CA": 0.0725,
    "US-NY": 0.04,
    "US-TX": 0.0625,
    "US-WA": 0.065,
    "US-OR": 0.0,
    "CA-ON": 0.13,
    "CA-BC": 0.12,
}


def calculate_tax(region: str, amount_cents: int) -> int:
    """Sales tax in cents for an amount, rounded half up. Unknown regions are an error.

    Amounts of zero or less (fully discounted orders) are never taxed.
    """
    if region not in TAX_RATES:
        raise ValueError(f"no tax rate configured for region {region}")
    if amount_cents <= 0:
        return 0
    return int(amount_cents * TAX_RATES[region] + 0.5)


def tax_rate(region: str) -> float:
    """The tax rate for a region, or 0 when the region is not configured."""
    return TAX_RATES.get(region, 0.0)
