import pytest

from number_converter import convert_to_polish_name

@pytest.mark.parametrize(
    ("number", "expected"),
    [
        (0, "zero"),
        (1, "jeden"),
        
    ]
)

def test_convert_valid_number(number, expected):
    assert convert_to_polish_name(number) == expected
    
@pytest.mark.parametrize("number", [-1_000_000, 1_000_000])
def test_convert_number_outside_range(number):
    with pytest.raises(ValueError):
        convert_to_polish_name(number=number)