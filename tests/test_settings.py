from pathlib import Path

import pytest

from weatherbot.settings import load_settings


def test_wallet_and_telegram_keys_are_refused(tmp_path: Path):
    path = tmp_path / "paper.yaml"
    path.write_text(
        """
capital_usd: 1000
max_order_usd: 25
min_order_usd: 1
min_edge: 0.05
max_spread: 0.10
forecast_days: 1
tail_lower_bound_c: -80
tail_upper_bound_c: 60
sigma_floor_c: 0.4
sigma_per_sqrt_day_c: 0.8
ensemble_model: ncep_gefs025
database: data/weatherbot.sqlite
private_key: "0xabc"
telegram_token: "123:abc"
""",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="forbidden"):
        load_settings(str(path))
