from typing import Literal

TemperatureUnit = Literal["celsius", "fahrenheit"]


def celsius_to(unit: TemperatureUnit, temp_c: float) -> float:
    if unit == "celsius":
        return temp_c
    if unit == "fahrenheit":
        return temp_c * 9.0 / 5.0 + 32.0
    raise ValueError(f"unknown unit {unit}")


def unit_from_letter(letter: str) -> TemperatureUnit:
    key = letter.strip().lower()
    if key == "c":
        return "celsius"
    if key == "f":
        return "fahrenheit"
    raise ValueError(f"unknown temperature letter {letter}")
