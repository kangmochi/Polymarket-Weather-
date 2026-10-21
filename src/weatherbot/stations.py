from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from weatherbot.settings import reject_forbidden_keys


@dataclass(frozen=True)
class Station:
    id: str
    city: str
    title_city: str
    slug_city: str
    timezone: str
    latitude: float
    longitude: float
    settlement_station_id: str


def load_stations(path: str) -> list[Station]:
    loaded = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    reject_forbidden_keys(loaded)
    if not isinstance(loaded, dict) or not isinstance(loaded.get("stations"), list):
        raise ValueError("stations file must contain a stations list")
    stations: list[Station] = []
    seen: set[str] = set()
    for index, row in enumerate(loaded["stations"]):
        if not isinstance(row, dict):
            raise ValueError(f"station {index} must be a mapping")
        station = _station(row, index)
        if station.id in seen:
            raise ValueError(f"duplicate station id {station.id}")
        seen.add(station.id)
        stations.append(station)
    if not stations:
        raise ValueError("at least one station is required")
    return stations


def _station(row: dict[str, object], index: int) -> Station:
    station_id = _text(row, "id", index)
    timezone_name = _text(row, "timezone", index)
    try:
        ZoneInfo(timezone_name)
    except Exception as exc:
        raise ValueError(f"station {station_id} has unknown timezone {timezone_name}") from exc
    latitude = _coord(row, "latitude", -90.0, 90.0, station_id)
    longitude = _coord(row, "longitude", -180.0, 180.0, station_id)
    settlement = _text(row, "settlement_station_id", index).upper()
    if len(settlement) != 4 or not settlement.isalnum():
        raise ValueError(f"station {station_id} settlement id must be a 4-character ICAO id")
    slug_city = _text(row, "slug_city", index).lower()
    if not slug_city.replace("-", "").isalnum():
        raise ValueError(f"station {station_id} slug_city must be lowercase words")
    return Station(
        id=station_id,
        city=_text(row, "city", index),
        title_city=_text(row, "title_city", index),
        slug_city=slug_city,
        timezone=timezone_name,
        latitude=latitude,
        longitude=longitude,
        settlement_station_id=settlement,
    )


def _text(row: dict[str, object], key: str, index: int) -> str:
    value = row.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"station {index} missing {key}")
    return value.strip()


def _coord(row: dict[str, object], key: str, low: float, high: float, station_id: str) -> float:
    value = row.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"station {station_id} {key} must be a number")
    number = float(value)
    if not low <= number <= high:
        raise ValueError(f"station {station_id} {key} out of range")
    return number
