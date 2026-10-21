KELLY_FRACTION = 0.25
CAPITAL_FRACTION = 0.05


def order_notional(
    probability: float,
    price: float,
    side: str,
    capital_usd: float,
    max_order_usd: float,
    capital_fraction: float = CAPITAL_FRACTION,
) -> float:
    """Smaller of quarter-Kelly, the dollar cap, and capital_fraction of the bankroll."""
    if capital_usd <= 0 or max_order_usd <= 0 or capital_fraction <= 0:
        return 0.0
    kelly = _quarter_kelly_notional(probability, price, side, capital_usd)
    return min(kelly, max_order_usd, capital_fraction * capital_usd)


def contracts_for(side: str, notional_usd: float, price: float) -> float:
    if notional_usd <= 0:
        return 0.0
    if side == "buy":
        if not 0.0 < price < 1.0:
            raise ValueError("buy price must be inside (0, 1)")
        return notional_usd / price
    if side == "sell":
        if not 0.0 < price < 1.0:
            raise ValueError("sell price must be inside (0, 1)")
        # Dollars at risk when selling YES at the bid equal contracts * (1 - bid).
        return notional_usd / (1.0 - price)
    raise ValueError(f"unknown side {side}")


def _quarter_kelly_notional(probability: float, price: float, side: str, capital_usd: float) -> float:
    if side == "buy":
        full = (probability - price) / (1.0 - price)
    elif side == "sell":
        full = (price - probability) / price
    else:
        raise ValueError(f"unknown side {side}")
    if full <= 0.0:
        return 0.0
    return full * KELLY_FRACTION * capital_usd
