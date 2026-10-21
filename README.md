# Polymarket weather bot, paper mode

This bot trades only daily temperature markets. The decision path is arithmetic: the fraction of ensemble members, a sigma that widens with lead time, then the book price. There is no LLM and no machine-learning model.

The default mode is paper. The process does not hold a private key and does not send live orders. Telegram sends one scan digest and, when a market resolves, one settlement digest of win and loss.

## Flow

1. `config/stations.yaml` stores the city, time zone, settlement-station coordinates, and ICAO id. `title_city` and `slug_city` are used only to find the event, not to compute a probability.
2. The collector stores every GFS ensemble member (`ncep_gefs025`) from Open-Meteo. Aviation Weather METAR is only today's observation at that station. TAF is not requested. METAR is not a forecast: on the same local date, the observed temperature is a floor on the daily maximum.
3. A bucket probability is the average of each member's mass inside the bucket bounds. Sigma = `sigma_floor_c + sigma_per_sqrt_day_c * sqrt(lead_days)`, converted into the market unit. Those coefficients are configuration, not a fitted model.
4. `or higher` / `or below` buckets have explicit far bounds (`tail_lower_bound_c`, `tail_upper_bound_c`). Mass outside those bounds is not given to the open bucket. Whole degrees from the resolution source map to a half-degree window, so `56-57°F` becomes `[55.5, 57.5)`.
5. The CLOB book is read separately. A buy uses the best ask. A sell uses the best bid. Depth is the size at that price. Last price is not read. A book missing one side, or a spread above `max_spread`, rejects the signal before edge is computed, so the edge column stays empty.
6. Buy edge is probability minus ask. Sell edge is bid minus probability. Size is the minimum of quarter-Kelly, `max_order_usd`, and 5% of starting capital. One position per bucket. A sell locks capital equal to the notional, contracts times `(1 - bid)`.
7. Paper execution is separate from research. The idempotency key is a hash of station, date, token, and side, so a repeat scan does not add another order. Short depth is stored as an `unfilled` order and does not change cash. A `filled` order does.
8. SQLite stores every signal, including rejects: fetch time, station, probability, bid, ask, and reason. A position whose market has resolved moves to `paper_settlements` with a win or loss, payout, and pnl. Paper cash increases by the payout. `portfolio` reports cash, equity, and open pnl. The `city_bias` table exists and starts empty.

## Run

```bash
git clone https://github.com/kangmochi/Polymarket-Weather-.git
cd Polymarket-Weather-
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest
python -m weatherbot collect
python -m weatherbot scan
python -m weatherbot status
python -m weatherbot portfolio
```

Later updates from this clone:

```bash
git pull
```

`capital_usd` in `config/paper.yaml` is the paper starting capital and should match the live capital you plan to use. `max_order_usd` is the dollar cap per order.

The default database is `data/weatherbot.sqlite`. That file is not committed.

## Timer

`./deploy/install.sh` creates the virtualenv and installs a systemd timer, `polymarket-weather-bot.timer`, that runs `weatherbot scan` every 30 minutes as the current user. The service writes only under `data/`. The unit files in this repository use `@INSTALL_DIR@` and `@SERVICE_USER@`. The install script fills those in from this clone. It does not embed a home directory.

Status and logs:

```bash
systemctl status polymarket-weather-bot.timer
journalctl -u polymarket-weather-bot.service -n 50
.venv/bin/weatherbot status
.venv/bin/weatherbot portfolio
```

## Telegram

Order notices are read from a local file, not from `config/paper.yaml`. The token is not committed. `config/telegram.env` is gitignored; copy the example and fill it in on your machine.

```bash
cp config/telegram.env.example config/telegram.env
```

Put `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` in `config/telegram.env`. Get the token from @BotFather. For the chat id, open the bot, send `/start`, then open `https://api.telegram.org/bot<TOKEN>/getUpdates` and copy `chat.id`.

A scan that places orders sends one digest at the end, not one message per order. The digest counts filled and unfilled orders. A resolved market adds one settlement digest of win and loss, and the result is stored in SQLite. A scan with no new order and no settlement sends nothing. Telegram does not choose orders and does not hold a wallet key.

## Left out on purpose

- Wallet keys, live orders, and signatures.
- City-bias updates. That waits on settlement, and the model does not read the bias table yet.
