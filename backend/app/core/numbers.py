"""Small number parsing helpers shared by indexing and planning."""
from __future__ import annotations


def chinese_number_to_int(text: str) -> int | None:
    """Parse Arabic digits or a common Chinese integer representation."""
    value = str(text or "").strip()
    if not value:
        return None
    if value.isdigit():
        return int(value)

    digits = {
        "零": 0,
        "〇": 0,
        "一": 1,
        "二": 2,
        "两": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
    }
    units = {"十": 10, "百": 100, "千": 1000, "万": 10000}
    total = 0
    section = 0
    number = 0
    for char in value:
        if char in digits:
            number = digits[char]
            continue
        unit = units.get(char)
        if unit is None:
            return None
        if unit == 10000:
            total += (section + number) * unit
            section = 0
            number = 0
        else:
            section += (number or 1) * unit
            number = 0
    return total + section + number
