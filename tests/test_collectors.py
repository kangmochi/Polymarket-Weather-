from datetime import datetime, timezone

from weatherbot.stations import Station
from weatherbot.weather.ensemble import ensemble_url, parse_ensemble
from weatherbot.weather.metar import metar_url, parse_metar_today

STATION = Station(
    id="nyc",
    city="New York",
    title_city="NYC",
    slug_city="nyc",
    timezone="America/New_York",
    latitude=40.7794,
    longitude=-73.8803,
    settlement_station_id="KLGA",
)
NOW = datetime(2026, 10, 20, 16, 0, tzinfo=timezone.utc)


def test_ensemble_keeps_every_non_null_gfs_member_and_requests_only_gefs():
    url = ensemble_url(STATION, "ncep_gefs025", 3)
    assert "models=ncep_gefs025" in url
    assert "daily=temperature_2m_max" in url
    assert "ecmwf" not in url
    payload = {
        "daily": {
            "time": ["2026-10-20", "2026-10-21"],
            "temperature_2m_max_member01": [20.0, 18.5],
            "temperature_2m_max_member02": [None, 19.0],
            "temperature_2m_max_member03": [21.5, 17.0],
        }
    }
    snapshot = parse_ensemble(payload, station_id="nyc", model="ncep_gefs025", fetched_at="2026-10-20T16:00:00Z")
    assert len(snapshot.members) == 5
    assert snapshot.celsius_on(snapshot.members[0].valid_date)[0] == 20.0
    names = {member.member_name for member in snapshot.members}
    assert names == {"member01", "member02", "member03"}


def test_metar_keeps_today_only_and_does_not_request_a_forecast():
    url = metar_url("KLGA")
    assert url.startswith("https://aviationweather.gov/api/data/metar?")
    assert "taf=false" in url
    assert "/taf" not in url
    today = int(datetime(2026, 10, 20, 15, 0, tzinfo=timezone.utc).timestamp())
    yesterday = int(datetime(2026, 10, 19, 15, 0, tzinfo=timezone.utc).timestamp())
    payload = [
        {"icaoId": "KLGA", "obsTime": yesterday, "temp": 30, "rawOb": "OLD"},
        {"icaoId": "KLGA", "obsTime": today, "temp": 13.3, "rawOb": "METAR KLGA"},
        {"icaoId": "KORD", "obsTime": today, "temp": 8, "rawOb": "OTHER"},
        {"icaoId": "KLGA", "obsTime": today, "temp": None, "rawOb": "NO TEMP"},
    ]
    kept = parse_metar_today(payload, station=STATION, now=NOW)
    assert len(kept) == 1
    assert kept[0].temperature_c == 13.3
    assert kept[0].local_date.isoformat() == "2026-10-20"
    assert kept[0].raw_text == "METAR KLGA"
