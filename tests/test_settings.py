from pathlib import Path

import pytest

from weatherbot.settings import load_settings

_PAPER = """
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
"""


@pytest.mark.parametrize(
    "key,value",
    [
        ("private_key", "0xabc"),
        ("api_secret", "secret"),
        ("passphrase", "phrase"),
        ("telegram_token", "123:abc"),
    ],
)
def test_wallet_and_telegram_keys_are_refused(tmp_path: Path, key: str, value: str):
    path = tmp_path / "paper.yaml"
    path.write_text(_PAPER + f'{key}: "{value}"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="forbidden"):
        load_settings(str(path))


def test_env_examples_have_empty_secrets_and_are_ignored():
    gitignore = Path(".gitignore").read_text(encoding="utf-8")
    assert "config/telegram.env" in gitignore
    assert "config/wallet.env" in gitignore
    telegram = Path("config/telegram.env.example").read_text(encoding="utf-8")
    wallet = Path("config/wallet.env.example").read_text(encoding="utf-8")
    assert telegram == "TELEGRAM_BOT_TOKEN=\nTELEGRAM_CHAT_ID=\n"
    assert wallet == (
        "TRADING_MODE=paper\n"
        "POLYMARKET_PRIVATE_KEY=\n"
        "POLYMARKET_FUNDER_ADDRESS=\n"
        "POLYMARKET_SIGNATURE_TYPE=2\n"
        "POLYMARKET_CHAIN_ID=137\n"
        "POLYMARKET_CLOB_HOST=https://clob.polymarket.com\n"
    )
    assert not (Path("config") / "telegram.env").exists()
    assert not (Path("config") / "wallet.env").exists()
