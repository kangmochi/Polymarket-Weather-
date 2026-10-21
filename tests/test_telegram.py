from pathlib import Path

from weatherbot.notify.telegram import TelegramNotifier, load_notifier, parse_env_file


class RecordingHttp:
    def __init__(self) -> None:
        self.posts: list[tuple[str, dict]] = []

    def post_json(self, url: str, body: dict):
        self.posts.append((url, body))
        return {"ok": True}


def test_env_file_parses_comments_and_quotes():
    values = parse_env_file(
        """
# note
TELEGRAM_BOT_TOKEN="123:abc"
TELEGRAM_CHAT_ID='99'
"""
    )
    assert values["TELEGRAM_BOT_TOKEN"] == "123:abc"
    assert values["TELEGRAM_CHAT_ID"] == "99"


def test_missing_or_empty_config_sends_nothing(tmp_path: Path):
    http = RecordingHttp()
    assert load_notifier(str(tmp_path / "missing.env"), http) is None
    empty = tmp_path / "telegram.env"
    empty.write_text("TELEGRAM_BOT_TOKEN=\nTELEGRAM_CHAT_ID=\n", encoding="utf-8")
    assert load_notifier(str(empty), http) is None
    assert http.posts == []


def test_order_notice_goes_to_the_chat_and_leaves_the_token_out_of_the_body(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    path = tmp_path / "telegram.env"
    path.write_text("TELEGRAM_BOT_TOKEN=123:secret\nTELEGRAM_CHAT_ID=555\n", encoding="utf-8")
    http = RecordingHttp()
    notifier = load_notifier(str(path), http)
    assert notifier is not None
    notifier.notify_order(
        city="New York",
        valid_date="2026-10-21",
        bucket_label="68-69°F",
        side="buy",
        price=0.4,
        notional_usd=25,
        status="filled",
    )
    assert http.posts == []
    notifier.flush()
    url, body = http.posts[0]
    assert url.endswith("/bot123:secret/sendMessage")
    assert body["chat_id"] == "555"
    assert "Paper scan: 1 filled, 0 unfilled" in body["text"]
    assert "68-69°F buy 0.4000 $25.00 filled" in body["text"]
    assert "123:secret" not in body["text"]


def test_one_digest_lists_filled_and_unfilled_orders():
    http = RecordingHttp()
    notifier = TelegramNotifier(token="t", chat_id="1", http=http)
    notifier.notify_order(
        city="Miami",
        valid_date="2026-10-22",
        bucket_label="82-83°F",
        side="sell",
        price=0.41,
        notional_usd=25,
        status="filled",
    )
    notifier.notify_order(
        city="Miami",
        valid_date="2026-10-22",
        bucket_label="76-79°F",
        side="buy",
        price=0.018,
        notional_usd=25,
        status="unfilled",
    )
    notifier.notify_order(
        city="Atlanta",
        valid_date="2026-10-20",
        bucket_label="72-73°F",
        side="buy",
        price=0.04,
        notional_usd=19.74,
        status="filled",
    )
    notifier.flush()
    notifier.flush()
    assert len(http.posts) == 1
    text = http.posts[0][1]["text"]
    assert text.startswith("Paper scan: 2 filled, 1 unfilled")
    assert "Miami 2026-10-22" in text
    assert "82-83°F sell 0.4100 $25.00 filled" in text
    assert "76-79°F buy 0.0180 $25.00 unfilled" in text
    assert "Atlanta 2026-10-20" in text


def test_scan_without_orders_sends_nothing():
    http = RecordingHttp()
    notifier = TelegramNotifier(token="t", chat_id="1", http=http)
    notifier.notify_order(
        city="London",
        valid_date="2026-10-21",
        bucket_label="12°C",
        side="sell",
        price=0.2,
        notional_usd=10,
        status="ignored",
    )
    notifier.flush()
    assert http.posts == []


def test_example_file_has_no_token_and_real_file_is_ignored():
    example = Path("config/telegram.env.example").read_text(encoding="utf-8")
    assert "TELEGRAM_BOT_TOKEN=" in example
    assert "123456:" not in example
    assert "config/telegram.env" in Path(".gitignore").read_text(encoding="utf-8")
