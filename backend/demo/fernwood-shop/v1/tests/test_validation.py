import pytest

from fernwood.customers.validation import normalize_phone, validate_email


def test_phone_numbers_are_normalised():
    assert normalize_phone("(555) 123-4567") == "+15551234567"
    assert normalize_phone("+44 20 7946 0958") == "+442079460958"


def test_invalid_email_is_rejected():
    with pytest.raises(ValueError):
        validate_email("not-an-email")
