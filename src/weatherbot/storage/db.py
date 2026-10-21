import sqlite3
from pathlib import Path
from typing import Any, Iterable

SCHEMA = """
CREATE TABLE IF NOT EXISTS ensemble_fetches (
  id INTEGER PRIMARY KEY,
  fetched_at TEXT NOT NULL,
  station_id TEXT NOT NULL,
  model TEXT NOT NULL,
  member_count INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS ensemble_members (
  id INTEGER PRIMARY KEY,
  fetch_id INTEGER NOT NULL REFERENCES ensemble_fetches(id),
  station_id TEXT NOT NULL,
  valid_date TEXT NOT NULL,
  member_name TEXT NOT NULL,
  temperature_c REAL NOT NULL,
  UNIQUE (fetch_id, valid_date, member_name)
);

CREATE TABLE IF NOT EXISTS metar_observations (
  id INTEGER PRIMARY KEY,
  fetched_at TEXT NOT NULL,
  station_id TEXT NOT NULL,
  settlement_station_id TEXT NOT NULL,
  observed_at TEXT NOT NULL,
  local_date TEXT NOT NULL,
  temperature_c REAL NOT NULL,
  raw_text TEXT NOT NULL,
  UNIQUE (settlement_station_id, observed_at)
);

CREATE TABLE IF NOT EXISTS signals (
  id INTEGER PRIMARY KEY,
  weather_fetched_at TEXT,
  book_fetched_at TEXT,
  station_id TEXT NOT NULL,
  city TEXT NOT NULL,
  settlement_station_id TEXT NOT NULL,
  valid_date TEXT NOT NULL,
  condition_id TEXT,
  token_id TEXT,
  bucket_label TEXT,
  bound_low REAL,
  bound_high REAL,
  tail TEXT,
  model_probability REAL NOT NULL,
  sigma REAL,
  member_count INTEGER,
  lead_hours REAL,
  best_bid REAL,
  best_ask REAL,
  bid_depth REAL,
  ask_depth REAL,
  spread REAL,
  side TEXT,
  reference_price REAL,
  edge REAL,
  notional_usd REAL,
  decision TEXT NOT NULL CHECK (decision IN ('accepted', 'rejected')),
  reason TEXT NOT NULL,
  execution_status TEXT,
  execution_reason TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS paper_account (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  starting_capital_usd REAL NOT NULL,
  cash_usd REAL NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS paper_orders (
  id INTEGER PRIMARY KEY,
  signal_id INTEGER NOT NULL REFERENCES signals(id),
  idempotency_key TEXT NOT NULL UNIQUE,
  bucket_key TEXT NOT NULL,
  station_id TEXT NOT NULL,
  valid_date TEXT NOT NULL,
  side TEXT NOT NULL,
  limit_price REAL NOT NULL,
  notional_usd REAL NOT NULL,
  contracts REAL,
  status TEXT NOT NULL CHECK (status IN ('filled', 'unfilled')),
  reason TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS paper_positions (
  id INTEGER PRIMARY KEY,
  bucket_key TEXT NOT NULL UNIQUE,
  station_id TEXT NOT NULL,
  city TEXT NOT NULL,
  valid_date TEXT NOT NULL,
  token_id TEXT NOT NULL,
  bucket_label TEXT NOT NULL,
  side TEXT NOT NULL,
  contracts REAL NOT NULL,
  price REAL NOT NULL,
  notional_usd REAL NOT NULL,
  opened_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS paper_settlements (
  id INTEGER PRIMARY KEY,
  bucket_key TEXT NOT NULL UNIQUE,
  station_id TEXT NOT NULL,
  city TEXT NOT NULL,
  valid_date TEXT NOT NULL,
  token_id TEXT NOT NULL,
  bucket_label TEXT NOT NULL,
  side TEXT NOT NULL CHECK (side IN ('buy', 'sell')),
  contracts REAL NOT NULL,
  entry_price REAL NOT NULL,
  notional_usd REAL NOT NULL,
  yes_won INTEGER NOT NULL CHECK (yes_won IN (0, 1)),
  result TEXT NOT NULL CHECK (result IN ('win', 'loss')),
  payout_usd REAL NOT NULL,
  pnl_usd REAL NOT NULL,
  opened_at TEXT NOT NULL,
  settled_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS live_orders (
  id INTEGER PRIMARY KEY,
  signal_id INTEGER NOT NULL REFERENCES signals(id),
  idempotency_key TEXT NOT NULL UNIQUE,
  bucket_key TEXT NOT NULL,
  station_id TEXT NOT NULL,
  valid_date TEXT NOT NULL,
  side TEXT NOT NULL,
  limit_price REAL NOT NULL,
  notional_usd REAL NOT NULL,
  contracts REAL,
  status TEXT NOT NULL CHECK (status IN ('filled', 'unfilled')),
  reason TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS city_bias (
  city TEXT PRIMARY KEY,
  bias_degrees REAL,
  unit TEXT,
  sample_count INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT
);

CREATE TABLE IF NOT EXISTS fetch_errors (
  id INTEGER PRIMARY KEY,
  fetched_at TEXT NOT NULL,
  source TEXT NOT NULL,
  station_id TEXT,
  detail TEXT NOT NULL
);
"""


class Database:
    def __init__(self, path: str) -> None:
        self.path = path
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)

    def close(self) -> None:
        self.conn.close()

    def ensure_account(self, capital_usd: float, now_iso: str) -> None:
        row = self.conn.execute("SELECT id FROM paper_account WHERE id = 1").fetchone()
        if row is None:
            self.conn.execute(
                """
                INSERT INTO paper_account (id, starting_capital_usd, cash_usd, updated_at)
                VALUES (1, ?, ?, ?)
                """,
                (capital_usd, capital_usd, now_iso),
            )
            self.conn.commit()

    def cash_usd(self) -> float:
        row = self._account()
        return float(row["cash_usd"])

    def starting_capital_usd(self) -> float:
        row = self._account()
        return float(row["starting_capital_usd"])

    def position_open(self, bucket_key: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM paper_positions WHERE bucket_key = ?",
            (bucket_key,),
        ).fetchone()
        return row is not None

    def order_exists(self, idempotency_key: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM paper_orders WHERE idempotency_key = ?",
            (idempotency_key,),
        ).fetchone()
        return row is not None

    def live_order_exists(self, idempotency_key: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM live_orders WHERE idempotency_key = ?",
            (idempotency_key,),
        ).fetchone()
        return row is not None

    def live_position_open(self, bucket_key: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM live_orders WHERE bucket_key = ? AND status = 'filled'",
            (bucket_key,),
        ).fetchone()
        return row is not None

    def live_reserved_usd(self) -> float:
        row = self.conn.execute(
            "SELECT COALESCE(SUM(notional_usd), 0) AS n FROM live_orders WHERE status = 'filled'"
        ).fetchone()
        return float(row["n"])

    def record_live_order(
        self,
        *,
        signal_id: int,
        idempotency_key: str,
        bucket_key: str,
        station_id: str,
        valid_date: str,
        side: str,
        limit_price: float,
        notional_usd: float,
        contracts: float,
        status: str,
        reason: str,
        created_at: str,
    ) -> None:
        """Store a live result without moving paper cash."""
        try:
            self.conn.execute("BEGIN IMMEDIATE")
            existing = self.conn.execute(
                "SELECT 1 FROM live_orders WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            if existing is not None:
                self.conn.rollback()
                raise LookupError("duplicate_order")
            self.conn.execute(
                """
                INSERT INTO live_orders (
                  signal_id, idempotency_key, bucket_key, station_id, valid_date,
                  side, limit_price, notional_usd, contracts, status, reason, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    signal_id,
                    idempotency_key,
                    bucket_key,
                    station_id,
                    valid_date,
                    side,
                    limit_price,
                    notional_usd,
                    contracts,
                    status,
                    reason,
                    created_at,
                ),
            )
            self.conn.execute(
                """
                UPDATE signals
                SET execution_status = ?, execution_reason = ?
                WHERE id = ?
                """,
                (status, reason, signal_id),
            )
            self.conn.commit()
        except Exception:
            if self.conn.in_transaction:
                self.conn.rollback()
            raise

    def bias_count(self) -> int:
        row = self.conn.execute("SELECT COUNT(*) AS n FROM city_bias").fetchone()
        return int(row["n"])

    def insert_fetch_error(
        self, fetched_at: str, source: str, station_id: str | None, detail: str
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO fetch_errors (fetched_at, source, station_id, detail)
            VALUES (?, ?, ?, ?)
            """,
            (fetched_at, source, station_id, detail),
        )
        self.conn.commit()

    def insert_ensemble(
        self,
        fetched_at: str,
        station_id: str,
        model: str,
        members: Iterable[tuple[str, str, float]],
    ) -> int:
        rows = list(members)
        names = {name for _date, name, _temp in rows}
        cursor = self.conn.execute(
            """
            INSERT INTO ensemble_fetches (fetched_at, station_id, model, member_count)
            VALUES (?, ?, ?, ?)
            """,
            (fetched_at, station_id, model, len(names)),
        )
        fetch_id = int(cursor.lastrowid)
        self.conn.executemany(
            """
            INSERT INTO ensemble_members
              (fetch_id, station_id, valid_date, member_name, temperature_c)
            VALUES (?, ?, ?, ?, ?)
            """,
            [(fetch_id, station_id, valid_date, name, temp) for valid_date, name, temp in rows],
        )
        self.conn.commit()
        return fetch_id

    def latest_members(self, station_id: str, valid_date: str) -> list[float]:
        rows = self.conn.execute(
            """
            SELECT temperature_c
            FROM ensemble_members
            WHERE station_id = ? AND valid_date = ?
              AND fetch_id = (
                SELECT MAX(id) FROM ensemble_fetches WHERE station_id = ?
              )
            ORDER BY member_name
            """,
            (station_id, valid_date, station_id),
        ).fetchall()
        return [float(row["temperature_c"]) for row in rows]

    def insert_metar(
        self,
        fetched_at: str,
        station_id: str,
        settlement_station_id: str,
        observed_at: str,
        local_date: str,
        temperature_c: float,
        raw_text: str,
    ) -> None:
        self.conn.execute(
            """
            INSERT OR IGNORE INTO metar_observations (
              fetched_at, station_id, settlement_station_id, observed_at,
              local_date, temperature_c, raw_text
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                fetched_at,
                station_id,
                settlement_station_id,
                observed_at,
                local_date,
                temperature_c,
                raw_text,
            ),
        )
        self.conn.commit()

    def metar_max_c(self, station_id: str, local_date: str) -> float | None:
        row = self.conn.execute(
            """
            SELECT MAX(temperature_c) AS max_c
            FROM metar_observations
            WHERE station_id = ? AND local_date = ?
            """,
            (station_id, local_date),
        ).fetchone()
        if row is None or row["max_c"] is None:
            return None
        return float(row["max_c"])

    def insert_signal(self, fields: dict[str, Any]) -> int:
        columns = list(fields)
        placeholders = ", ".join("?" for _ in columns)
        names = ", ".join(columns)
        cursor = self.conn.execute(
            f"INSERT INTO signals ({names}) VALUES ({placeholders})",
            [fields[name] for name in columns],
        )
        self.conn.commit()
        return int(cursor.lastrowid)

    def mark_execution(self, signal_id: int, status: str, reason: str) -> None:
        self.conn.execute(
            """
            UPDATE signals
            SET execution_status = ?, execution_reason = ?
            WHERE id = ?
            """,
            (status, reason, signal_id),
        )
        self.conn.commit()

    def fill_order(
        self,
        signal_id: int,
        idempotency_key: str,
        bucket_key: str,
        station_id: str,
        city: str,
        valid_date: str,
        token_id: str,
        bucket_label: str,
        side: str,
        limit_price: float,
        notional_usd: float,
        contracts: float,
        depth: float,
        created_at: str,
    ) -> str:
        """Fill at the touch or record an unfilled order when depth is short.

        Returns 'filled' or 'unfilled'. Raises if the key or bucket is already used
        or cash cannot fund the order. Callers treat that as a skipped execution.
        """
        try:
            self.conn.execute("BEGIN IMMEDIATE")
            existing = self.conn.execute(
                "SELECT 1 FROM paper_orders WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            if existing is not None:
                self.conn.rollback()
                raise LookupError("duplicate_order")
            position = self.conn.execute(
                "SELECT 1 FROM paper_positions WHERE bucket_key = ?",
                (bucket_key,),
            ).fetchone()
            if position is not None:
                self.conn.rollback()
                raise LookupError("one_position_per_bucket")
            funded = self.conn.execute(
                "SELECT 1 FROM paper_account WHERE id = 1 AND cash_usd >= ?",
                (notional_usd,),
            ).fetchone()
            if funded is None:
                self.conn.rollback()
                raise LookupError("insufficient_cash")
            if contracts > depth:
                self.conn.execute(
                    """
                    INSERT INTO paper_orders (
                      signal_id, idempotency_key, bucket_key, station_id, valid_date,
                      side, limit_price, notional_usd, contracts, status, reason, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'unfilled', 'insufficient_depth', ?)
                    """,
                    (
                        signal_id,
                        idempotency_key,
                        bucket_key,
                        station_id,
                        valid_date,
                        side,
                        limit_price,
                        notional_usd,
                        contracts,
                        created_at,
                    ),
                )
                self.conn.execute(
                    """
                    UPDATE signals
                    SET execution_status = 'unfilled', execution_reason = 'insufficient_depth'
                    WHERE id = ?
                    """,
                    (signal_id,),
                )
                self.conn.commit()
                return "unfilled"
            updated = self.conn.execute(
                """
                UPDATE paper_account
                SET cash_usd = cash_usd - ?, updated_at = ?
                WHERE id = 1 AND cash_usd >= ?
                """,
                (notional_usd, created_at, notional_usd),
            )
            if updated.rowcount != 1:
                self.conn.rollback()
                raise LookupError("insufficient_cash")
            self.conn.execute(
                """
                INSERT INTO paper_orders (
                  signal_id, idempotency_key, bucket_key, station_id, valid_date,
                  side, limit_price, notional_usd, contracts, status, reason, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'filled', 'filled', ?)
                """,
                (
                    signal_id,
                    idempotency_key,
                    bucket_key,
                    station_id,
                    valid_date,
                    side,
                    limit_price,
                    notional_usd,
                    contracts,
                    created_at,
                ),
            )
            self.conn.execute(
                """
                INSERT INTO paper_positions (
                  bucket_key, station_id, city, valid_date, token_id, bucket_label,
                  side, contracts, price, notional_usd, opened_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    bucket_key,
                    station_id,
                    city,
                    valid_date,
                    token_id,
                    bucket_label,
                    side,
                    contracts,
                    limit_price,
                    notional_usd,
                    created_at,
                ),
            )
            self.conn.execute(
                """
                UPDATE signals
                SET execution_status = 'filled', execution_reason = 'filled'
                WHERE id = ?
                """,
                (signal_id,),
            )
            self.conn.commit()
            return "filled"
        except Exception:
            if self.conn.in_transaction:
                self.conn.rollback()
            raise

    def signal_rows(self) -> list[sqlite3.Row]:
        return list(self.conn.execute("SELECT * FROM signals ORDER BY id").fetchall())

    def order_rows(self) -> list[sqlite3.Row]:
        return list(self.conn.execute("SELECT * FROM paper_orders ORDER BY id").fetchall())

    def position_rows(self) -> list[sqlite3.Row]:
        return list(self.conn.execute("SELECT * FROM paper_positions ORDER BY id").fetchall())

    def settlement_rows(self) -> list[sqlite3.Row]:
        return list(self.conn.execute("SELECT * FROM paper_settlements ORDER BY id").fetchall())

    def record_settlement(
        self,
        bucket_key: str,
        *,
        yes_won: bool,
        result: str,
        payout_usd: float,
        pnl_usd: float,
        settled_at: str,
    ) -> bool:
        """Move one open position into settlement history and credit its payout."""
        try:
            self.conn.execute("BEGIN IMMEDIATE")
            position = self.conn.execute(
                "SELECT * FROM paper_positions WHERE bucket_key = ?",
                (bucket_key,),
            ).fetchone()
            if position is None:
                self.conn.rollback()
                return False
            self.conn.execute(
                """
                INSERT INTO paper_settlements (
                  bucket_key, station_id, city, valid_date, token_id, bucket_label,
                  side, contracts, entry_price, notional_usd, yes_won, result,
                  payout_usd, pnl_usd, opened_at, settled_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    position["bucket_key"],
                    position["station_id"],
                    position["city"],
                    position["valid_date"],
                    position["token_id"],
                    position["bucket_label"],
                    position["side"],
                    position["contracts"],
                    position["price"],
                    position["notional_usd"],
                    1 if yes_won else 0,
                    result,
                    payout_usd,
                    pnl_usd,
                    position["opened_at"],
                    settled_at,
                ),
            )
            self.conn.execute("DELETE FROM paper_positions WHERE bucket_key = ?", (bucket_key,))
            self.conn.execute(
                """
                UPDATE paper_account
                SET cash_usd = cash_usd + ?, updated_at = ?
                WHERE id = 1
                """,
                (payout_usd, settled_at),
            )
            self.conn.commit()
            return True
        except Exception:
            if self.conn.in_transaction:
                self.conn.rollback()
            raise

    def count_members(self, fetch_id: int) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) AS n FROM ensemble_members WHERE fetch_id = ?",
            (fetch_id,),
        ).fetchone()
        return int(row["n"])

    def metar_dates(self, station_id: str) -> list[str]:
        rows = self.conn.execute(
            """
            SELECT DISTINCT local_date FROM metar_observations
            WHERE station_id = ? ORDER BY local_date
            """,
            (station_id,),
        ).fetchall()
        return [str(row["local_date"]) for row in rows]

    def summary(self) -> str:
        decisions = self.conn.execute(
            """
            SELECT decision, reason, COUNT(*) AS n
            FROM signals
            GROUP BY decision, reason
            ORDER BY decision, reason
            """
        ).fetchall()
        orders = self.conn.execute(
            """
            SELECT status, reason, COUNT(*) AS n
            FROM paper_orders
            GROUP BY status, reason
            ORDER BY status, reason
            """
        ).fetchall()
        account = self.conn.execute(
            "SELECT starting_capital_usd, cash_usd FROM paper_account WHERE id = 1"
        ).fetchone()
        positions = self.conn.execute("SELECT COUNT(*) AS n FROM paper_positions").fetchone()
        errors = self.conn.execute("SELECT COUNT(*) AS n FROM fetch_errors").fetchone()
        lines = ["paper account"]
        if account is None:
            lines.append("  not seeded")
        else:
            lines.append(
                f"  capital {account['starting_capital_usd']:.2f}  cash {account['cash_usd']:.2f}"
            )
        lines.append(f"positions {positions['n']}  bias rows {self.bias_count()}  fetch errors {errors['n']}")
        settled = self.conn.execute(
            """
            SELECT
              SUM(CASE WHEN result = 'win' THEN 1 ELSE 0 END) AS wins,
              SUM(CASE WHEN result = 'loss' THEN 1 ELSE 0 END) AS losses,
              COALESCE(SUM(pnl_usd), 0) AS pnl
            FROM paper_settlements
            """
        ).fetchone()
        if settled["wins"] or settled["losses"]:
            lines.append(
                "settlements "
                f"win {int(settled['wins'] or 0)}  "
                f"loss {int(settled['losses'] or 0)}  "
                f"realized {float(settled['pnl']):+.2f}"
            )
        if errors["n"]:
            latest = self.conn.execute(
                """
                SELECT fetched_at, source, station_id, detail
                FROM fetch_errors
                ORDER BY id DESC
                LIMIT 1
                """
            ).fetchone()
            detail = " ".join(str(latest["detail"]).split())
            if len(detail) > 180:
                detail = detail[:177] + "..."
            station = latest["station_id"] or "-"
            lines.append(
                f"  last error {latest['fetched_at']} {latest['source']} {station}: {detail}"
            )
        lines.append("signals")
        if not decisions:
            lines.append("  none")
        for row in decisions:
            lines.append(f"  {row['decision']} {row['reason']}: {row['n']}")
        lines.append("orders")
        if not orders:
            lines.append("  none")
        for row in orders:
            lines.append(f"  {row['status']} {row['reason']}: {row['n']}")
        live_orders = self.conn.execute(
            """
            SELECT status, reason, COUNT(*) AS n
            FROM live_orders
            GROUP BY status, reason
            ORDER BY status, reason
            """
        ).fetchall()
        if live_orders:
            lines.append("live orders")
            for row in live_orders:
                lines.append(f"  {row['status']} {row['reason']}: {row['n']}")
        return "\n".join(lines)

    def _account(self) -> sqlite3.Row:
        row = self.conn.execute("SELECT * FROM paper_account WHERE id = 1").fetchone()
        if row is None:
            raise RuntimeError("paper account is not seeded")
        return row
