import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from urllib.parse import urlencode

from weatherbot.errors import WeatherbotError
from weatherbot.stations import Station
from weatherbot.timeutil import isoformat_z

MEMBER_KEY = re.compile(r"^temperature_2m_max_member(\d+)$")
ENSEMBLE_URL = "https://ensemble-api.open-meteo.com/v1/ensemble"


@dataclass(frozen=True)
class MemberTemperature:
    valid_date: date
    member_name: str
    temperature_c: float


@dataclass(frozen=True)
class EnsembleSnapshot:
    fetched_at: str
    station_id: str
    model: str
    members: tuple[MemberTemperature, ...]

    def celsius_on(self, valid_date: date) -> list[float]:
        return [member.temperature_c for member in self.members if member.valid_date == valid_date]


def ensemble_url(station: Station, model: str, forecast_days: int) -> str:
    query = urlencode(
        {
            "latitude": station.latitude,
            "longitude": station.longitude,
            "daily": "temperature_2m_max",
            "models": model,
            "forecast_days": forecast_days,
            "temperature_unit": "celsius",
            "timezone": station.timezone,
            "timeformat": "iso8601",
        }
    )
    return f"{ENSEMBLE_URL}?{query}"


def fetch_ensemble(http: Any, station: Station, model: str, forecast_days: int, now: datetime) -> EnsembleSnapshot:
    url = ensemble_url(station, model, forecast_days)
    try:
        payload = http.get_json(url)
    except WeatherbotError:
        raise
    except Exception as exc:
        raise WeatherbotError("ensemble", str(exc)) from exc
    return parse_ensemble(payload, station_id=station.id, model=model, fetched_at=isoformat_z(now))


def parse_ensemble(payload: object, station_id: str, model: str, fetched_at: str) -> EnsembleSnapshot:
    if not isinstance(payload, dict):
        raise WeatherbotError("ensemble", "response was not an object")
    if payload.get("error"):
        raise WeatherbotError("ensemble", str(payload.get("reason") or "request failed"))
    daily = payload.get("daily")
    if not isinstance(daily, dict):
        raise WeatherbotError("ensemble", "daily block missing")
    times = daily.get("time")
    if not isinstance(times, list) or not times:
        raise WeatherbotError("ensemble", "daily time axis missing")
    dates = [_parse_day(value) for value in times]
    member_keys = sorted(
        (key for key in daily if isinstance(key, str) and MEMBER_KEY.match(key)),
        key=lambda key: int(MEMBER_KEY.match(key).group(1)),  # type: ignore[union-attr]
    )
    if not member_keys:
        raise WeatherbotError("ensemble", "no GFS member columns in the response")
    members: list[MemberTemperature] = []
    for key in member_keys:
        column = daily.get(key)
        if not isinstance(column, list) or len(column) != len(dates):
            raise WeatherbotError("ensemble", f"{key} does not match the time axis")
        for valid_date, value in zip(dates, column):
            if value is None:
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise WeatherbotError("ensemble", f"{key} has a non-numeric temperature")
            members.append(
                MemberTemperature(
                    valid_date=valid_date,
                    member_name=key.removeprefix("temperature_2m_max_"),
                    temperature_c=float(value),
                )
            )
    if not members:
        raise WeatherbotError("ensemble", "every ensemble member was null")
    return EnsembleSnapshot(
        fetched_at=fetched_at,
        station_id=station_id,
        model=model,
        members=tuple(members),
    )


def _parse_day(value: object) -> date:
    if not isinstance(value, str) or len(value) < 10:
        raise WeatherbotError("ensemble", "daily time value was not a date")
    try:
        return date.fromisoformat(value[:10])
    except ValueError as exc:
        raise WeatherbotError("ensemble", f"bad date {value}") from exc
