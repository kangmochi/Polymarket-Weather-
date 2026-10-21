from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from weatherbot.errors import WeatherbotError
from weatherbot.stations import Station
from weatherbot.timeutil import isoformat_z, local_today

METAR_URL = "https://aviationweather.gov/api/data/metar"


@dataclass(frozen=True)
class MetarObservation:
    settlement_station_id: str
    observed_at: str
    local_date: date
    temperature_c: float
    raw_text: str


def metar_url(settlement_station_id: str) -> str:
    """Current observations only. TAF is explicitly off and the path is METAR."""
    query = urlencode(
        {
            "ids": settlement_station_id,
            "format": "json",
            "hours": "30",
            "taf": "false",
        }
    )
    return f"{METAR_URL}?{query}"


def fetch_metar_today(http: Any, station: Station, now: datetime) -> tuple[MetarObservation, ...]:
    url = metar_url(station.settlement_station_id)
    try:
        payload = http.get_json(url)
    except WeatherbotError:
        raise
    except Exception as exc:
        raise WeatherbotError("metar", str(exc)) from exc
    return parse_metar_today(payload, station=station, now=now)


def parse_metar_today(payload: object, station: Station, now: datetime) -> tuple[MetarObservation, ...]:
    if isinstance(payload, dict) and payload.get("error"):
        raise WeatherbotError("metar", str(payload.get("error")))
    if not isinstance(payload, list):
        raise WeatherbotError("metar", "response was not a list of observations")
    today = local_today(now, station.timezone)
    zone = ZoneInfo(station.timezone)
    kept: list[MetarObservation] = []
    for row in payload:
        if not isinstance(row, dict):
            continue
        icao = str(row.get("icaoId") or "").upper()
        if icao != station.settlement_station_id:
            continue
        observed = _observed_at(row.get("obsTime"))
        if observed is None:
            continue
        local_date = observed.astimezone(zone).date()
        if local_date != today:
            continue
        temp = row.get("temp")
        if isinstance(temp, bool) or not isinstance(temp, (int, float)):
            continue
        raw = row.get("rawOb")
        kept.append(
            MetarObservation(
                settlement_station_id=station.settlement_station_id,
                observed_at=isoformat_z(observed),
                local_date=local_date,
                temperature_c=float(temp),
                raw_text=raw if isinstance(raw, str) else "",
            )
        )
    kept.sort(key=lambda item: item.observed_at)
    return tuple(kept)


def observed_floor_c(observations: tuple[MetarObservation, ...]) -> float | None:
    if not observations:
        return None
    return max(item.temperature_c for item in observations)


def _observed_at(value: object) -> datetime | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return datetime.fromtimestamp(float(value), tz=timezone.utc)
