from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

MONTHS = (
    "january",
    "february",
    "march",
    "april",
    "may",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
)


def parse_now(value: str | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    text = value.strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def isoformat_z(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def local_today(moment: datetime, timezone_name: str) -> date:
    return moment.astimezone(ZoneInfo(timezone_name)).date()


def forecast_dates(moment: datetime, timezone_name: str, days: int) -> list[date]:
    if days < 1:
        raise ValueError("forecast_days must be at least 1")
    start = local_today(moment, timezone_name)
    return [start + timedelta(days=offset) for offset in range(days)]


def lead_hours(moment: datetime, valid_date: date, timezone_name: str) -> float:
    """Hours from now until the end of the station-local settlement day."""
    zone = ZoneInfo(timezone_name)
    end_local = datetime.combine(valid_date, time(23, 59, 59), tzinfo=zone)
    seconds = (end_local - moment.astimezone(zone)).total_seconds()
    return max(0.0, seconds / 3600.0)


def event_slug(slug_city: str, valid_date: date) -> str:
    month = MONTHS[valid_date.month - 1]
    return (
        f"highest-temperature-in-{slug_city}-on-{month}-"
        f"{valid_date.day}-{valid_date.year}"
    )
