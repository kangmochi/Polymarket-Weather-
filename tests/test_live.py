import sys
import types
from pathlib import Path

from weatherbot.cli import main
from weatherbot.execution.live import ClobSession
from weatherbot.wallet import LiveConfigError, Wallet, require_live_wallet, trading_mode

ROOT = Path(__file__).resolve().parents[1]


def test_trading_mode_defaults_to_paper(tmp_path: Path) -> None:
    missing = tmp_path / "wallet.env"
    assert trading_mode(str(missing)) == "paper"
    missing.write_text("TRADING_MODE=\nPOLYMARKET_PRIVATE_KEY=\n", encoding="utf-8")
    assert trading_mode(str(missing)) == "paper"
    missing.write_text("TRADING_MODE=paper\n", encoding="utf-8")
    assert trading_mode(str(missing)) == "paper"
    missing.write_text("TRADING_MODE=live\n", encoding="utf-8")
    assert trading_mode(str(missing)) == "live"


def test_rejected_trading_mode_names_the_accepted_values(tmp_path: Path) -> None:
    path = tmp_path / "wallet.env"
    path.write_text("TRADING_MODE=both\n", encoding="utf-8")
    try:
        trading_mode(str(path))
    except ValueError as exc:
        assert str(exc) == "TRADING_MODE must be paper or live"
    else:
        raise AssertionError("invalid mode was accepted")


def test_live_wallet_requires_both_secrets(tmp_path: Path) -> None:
    path = tmp_path / "wallet.env"
    path.write_text(
        "POLYMARKET_PRIVATE_KEY=\nPOLYMARKET_FUNDER_ADDRESS=0x0000000000000000000000000000000000000001\n",
        encoding="utf-8",
    )
    try:
        require_live_wallet(str(path))
    except LiveConfigError as exc:
        assert "no order was sent" in str(exc)
    else:
        raise AssertionError("missing key was accepted")


def test_session_signs_then_posts_to_the_clob_host() -> None:
    calls: list[tuple] = []

    class FakeClient:
        def __init__(self, **kwargs: object) -> None:
            calls.append(("init", kwargs))

        def create_or_derive_api_key(self) -> dict[str, str]:
            calls.append(("derive",))
            return {"api": "creds"}

        def set_api_creds(self, creds: object) -> None:
            calls.append(("creds", creds))

        def create_order(self, args: object) -> dict[str, object]:
            calls.append(("sign", args))
            return {"signed": True, "args": args}

        def post_order(self, signed: object, order_type: object) -> dict[str, bool]:
            calls.append(("post", signed, order_type))
            return {"success": True}

    class OrderArgs:
        def __init__(self, **kwargs: object) -> None:
            self.__dict__.update(kwargs)

    class Side:
        BUY = "BUY"
        SELL = "SELL"

    class OrderType:
        GTC = "GTC"

    module = types.ModuleType("py_clob_client_v2")
    module.ClobClient = FakeClient  # type: ignore[attr-defined]
    module.OrderArgs = OrderArgs  # type: ignore[attr-defined]
    module.Side = Side  # type: ignore[attr-defined]
    module.OrderType = OrderType  # type: ignore[attr-defined]
    previous = sys.modules.get("py_clob_client_v2")
    sys.modules["py_clob_client_v2"] = module
    try:
        wallet = Wallet(
            private_key="test-signer",
            funder_address="0x0000000000000000000000000000000000000001",
            signature_type=2,
            chain_id=137,
            clob_host="https://clob.example.test",
        )
        session = ClobSession(wallet)
        signed = session.create_order({"token_id": "token", "price": 0.4, "size": 10, "side": "BUY"})
        session.post_order(signed)
    finally:
        if previous is None:
            sys.modules.pop("py_clob_client_v2", None)
        else:
            sys.modules["py_clob_client_v2"] = previous
    assert calls[0][0] == "init"
    kwargs = calls[0][1]
    assert kwargs["host"] == "https://clob.example.test"
    assert kwargs["key"] == "test-signer"
    assert kwargs["funder"] == "0x0000000000000000000000000000000000000001"
    assert kwargs["signature_type"] == 2
    assert kwargs["chain_id"] == 137
    assert [name for name, *_rest in calls] == ["init", "derive", "creds", "sign", "post"]
    assert calls[4][1]["signed"] is True
    assert calls[4][2] == "GTC"
    assert "test-signer" not in repr(wallet)


def test_commands_choose_paper_or_live(tmp_path: Path, monkeypatch) -> None:
    seen: dict[str, str] = {}

    def fake_run_scan(*_args: object, **kwargs: object) -> str:
        seen["mode"] = str(kwargs["trading_mode"])
        return "summary"

    monkeypatch.setattr("weatherbot.cli.run_scan", fake_run_scan)
    paper = tmp_path / "paper.yaml"
    source = (ROOT / "config" / "paper.yaml").read_text(encoding="utf-8")
    paper.write_text(
        source.replace("database: data/weatherbot.sqlite", f"database: {tmp_path / 'bot.sqlite'}"),
        encoding="utf-8",
    )
    wallet = tmp_path / "wallet.env"
    wallet.write_text("TRADING_MODE=live\nPOLYMARKET_PRIVATE_KEY=\nPOLYMARKET_FUNDER_ADDRESS=\n", encoding="utf-8")
    stations = str(ROOT / "config" / "stations.yaml")
    common = ["--paper", str(paper), "--stations", stations, "--now", "2026-10-20T16:00:00Z"]
    assert main(["paper", *common]) == 0
    assert seen["mode"] == "paper"
    assert main(["scan", *common]) == 0
    assert seen["mode"] == "live"
    assert main(["live", *common]) == 0
    assert seen["mode"] == "live"


def test_live_command_stops_when_the_key_is_missing(tmp_path: Path, capsys) -> None:
    paper = tmp_path / "paper.yaml"
    source = (ROOT / "config" / "paper.yaml").read_text(encoding="utf-8")
    paper.write_text(
        source.replace("database: data/weatherbot.sqlite", f"database: {tmp_path / 'bot.sqlite'}"),
        encoding="utf-8",
    )
    code = main(
        [
            "live",
            "--paper",
            str(paper),
            "--stations",
            str(ROOT / "config" / "stations.yaml"),
            "--now",
            "2026-10-20T16:00:00Z",
        ]
    )
    assert code == 2
    assert "no order was sent" in capsys.readouterr().err
