from datetime import date

import pytest

from fernwood.pricing.discounts import apply_discounts, loyalty_discount, seasonal_discount
from fernwood.pricing.tax import calculate_tax


def test_tax_rounds_half_up():
    assert calculate_tax("US-CA", 1000) == 73


def test_unknown_region_is_rejected():
    with pytest.raises(ValueError):
        calculate_tax("XX-XX", 100)


def test_loyalty_discount_is_capped():
    assert loyalty_discount(1000, 500) == 200


def test_seasonal_discount_only_in_november():
    assert seasonal_discount(1000, date(2026, 11, 3)) == 100
    assert seasonal_discount(1000, date(2026, 10, 3)) == 0


def test_bulk_discount_applies_from_five_lines():
    assert apply_discounts(10_000, line_count=5) <= 9_500
