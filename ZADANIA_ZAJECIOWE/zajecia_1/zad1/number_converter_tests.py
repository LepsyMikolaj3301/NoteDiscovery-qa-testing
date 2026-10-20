import pytest

from number_converter import convert_to_polish_name


@pytest.mark.parametrize(
    ("number", "expected"),
    [
        # digits
        (0, "zero"),
        (1, "jeden"),
        (2, "dwa"),
        (9, "dziewięć"),
        # teens
        (10, "dziesięć"),
        (11, "jedenaście"),
        (15, "piętnaście"),
        (19, "dziewiętnaście"),
        # tens
        (20, "dwadzieścia"),
        (21, "dwadzieścia jeden"),
        (40, "czterdzieści"),
        (99, "dziewięćdziesiąt dziewięć"),
        # hundreds
        (100, "sto"),
        (101, "sto jeden"),
        (110, "sto dziesięć"),
        (200, "dwieście"),
        (300, "trzysta"),
        (400, "czterysta"),
        (500, "pięćset"),
        (999, "dziewięćset dziewięćdziesiąt dziewięć"),
        # thousands (polish declension: tysiąc / tysiące / tysięcy)
        (1_000, "tysiąc"),
        (1_001, "tysiąc jeden"),
        (2_000, "dwa tysiące"),
        (4_000, "cztery tysiące"),
        (5_000, "pięć tysięcy"),
        (12_000, "dwanaście tysięcy"),
        (22_000, "dwadzieścia dwa tysiące"),
        (25_000, "dwadzieścia pięć tysięcy"),
        (100_000, "sto tysięcy"),
        (123_456, "sto dwadzieścia trzy tysiące czterysta pięćdziesiąt sześć"),
        (999_999, "dziewięćset dziewięćdziesiąt dziewięć tysięcy dziewięćset dziewięćdziesiąt dziewięć"),
        # negatives
        (-1, "minus jeden"),
        (-999_999, "minus dziewięćset dziewięćdziesiąt dziewięć tysięcy dziewięćset dziewięćdziesiąt dziewięć"),
    ],
)
def test_convert_valid_number(number, expected):
    assert convert_to_polish_name(number) == expected


@pytest.mark.parametrize("number", [-1_000_000, 1_000_000])
def test_convert_number_outside_range(number):
    with pytest.raises(ValueError):
        convert_to_polish_name(number=number)
