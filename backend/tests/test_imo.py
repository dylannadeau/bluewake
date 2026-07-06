from app.resolution.resolver import is_valid_imo


def test_valid_imo_numbers():
    # Real IMO numbers with correct check digits
    assert is_valid_imo("9074729")  # example from IMO's own spec
    assert is_valid_imo("9321483")


def test_invalid_check_digit():
    assert not is_valid_imo("9074720")
    assert not is_valid_imo("9183526")


def test_malformed():
    assert not is_valid_imo(None)
    assert not is_valid_imo("")
    assert not is_valid_imo("123")
    assert not is_valid_imo("abcdefg")
    assert not is_valid_imo("90747290")
