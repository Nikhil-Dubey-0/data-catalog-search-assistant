"""Synthetic source warehouse.

Each SQLite file under ``<var>/warehouse`` plays the role of one warehouse schema
(sales, finance, hr, ...). The catalog never trusts hand-written lists of tables:
technical metadata is *harvested* from these databases (see ``harvest.py``), so the
warehouse is the source of truth for which tables/columns exist.

All data is synthetic and generated from a fixed seed, so runs are reproducible.
"""

from __future__ import annotations

import logging
import random
import sqlite3
from datetime import date, timedelta
from pathlib import Path

log = logging.getLogger(__name__)

# schema -> table -> [(column, declared type)]
WAREHOUSE: dict[str, dict[str, list[tuple[str, str]]]] = {
    "sales": {
        "customers": [
            ("customer_id", "INTEGER PRIMARY KEY"), ("full_name", "TEXT NOT NULL"),
            ("email", "TEXT"), ("phone", "TEXT"), ("city", "TEXT"), ("country", "TEXT"),
            ("segment", "TEXT"), ("signup_date", "DATE"),
        ],
        "orders": [
            ("order_id", "INTEGER PRIMARY KEY"), ("customer_id", "INTEGER NOT NULL"),
            ("order_date", "DATE NOT NULL"), ("status", "TEXT"), ("channel", "TEXT"),
            ("total_amount", "REAL"), ("currency", "TEXT"),
        ],
        "order_items": [
            ("order_item_id", "INTEGER PRIMARY KEY"), ("order_id", "INTEGER NOT NULL"),
            ("product_id", "INTEGER NOT NULL"), ("quantity", "INTEGER"),
            ("unit_price", "REAL"), ("discount_pct", "REAL"),
        ],
    },
    "product": {
        "products": [
            ("product_id", "INTEGER PRIMARY KEY"), ("product_name", "TEXT NOT NULL"),
            ("category_id", "INTEGER"), ("list_price", "REAL"), ("launch_date", "DATE"),
            ("is_active", "INTEGER"),
        ],
        "categories": [
            ("category_id", "INTEGER PRIMARY KEY"), ("category_name", "TEXT NOT NULL"),
            ("parent_category", "TEXT"),
        ],
    },
    "marketing": {
        "campaigns": [
            ("campaign_id", "INTEGER PRIMARY KEY"), ("campaign_name", "TEXT"),
            ("channel", "TEXT"), ("start_date", "DATE"), ("end_date", "DATE"),
            ("budget", "REAL"), ("spend", "REAL"),
        ],
        "web_sessions": [
            ("session_id", "INTEGER PRIMARY KEY"), ("visitor_id", "TEXT"),
            ("campaign_id", "INTEGER"), ("landing_page", "TEXT"), ("session_start", "DATETIME"),
            ("pageviews", "INTEGER"), ("converted", "INTEGER"),
        ],
    },
    "finance": {
        "invoices": [
            ("invoice_id", "INTEGER PRIMARY KEY"), ("order_id", "INTEGER"),
            ("invoice_date", "DATE"), ("due_date", "DATE"), ("amount", "REAL"),
            ("tax_amount", "REAL"), ("status", "TEXT"),
        ],
        "payments": [
            ("payment_id", "INTEGER PRIMARY KEY"), ("invoice_id", "INTEGER"),
            ("payment_date", "DATE"), ("amount", "REAL"), ("method", "TEXT"),
        ],
        "revenue_monthly": [
            ("month", "TEXT NOT NULL"), ("region", "TEXT NOT NULL"), ("gross_revenue", "REAL"),
            ("net_revenue", "REAL"), ("refunds", "REAL"),
        ],
    },
    "hr": {
        "employees": [
            ("employee_id", "INTEGER PRIMARY KEY"), ("full_name", "TEXT NOT NULL"),
            ("work_email", "TEXT"), ("department", "TEXT"), ("job_title", "TEXT"),
            ("hire_date", "DATE"), ("manager_id", "INTEGER"), ("salary", "REAL"),
        ],
        "payroll": [
            ("payroll_id", "INTEGER PRIMARY KEY"), ("employee_id", "INTEGER NOT NULL"),
            ("pay_period", "TEXT"), ("gross_pay", "REAL"), ("net_pay", "REAL"),
            ("bank_account_last4", "TEXT"),
        ],
    },
    "support": {
        "tickets": [
            ("ticket_id", "INTEGER PRIMARY KEY"), ("customer_id", "INTEGER"),
            ("created_at", "DATETIME"), ("channel", "TEXT"), ("category", "TEXT"),
            ("priority", "TEXT"), ("status", "TEXT"), ("resolved_at", "DATETIME"),
        ],
        "csat_responses": [
            ("response_id", "INTEGER PRIMARY KEY"), ("ticket_id", "INTEGER"),
            ("score", "INTEGER"), ("comment", "TEXT"), ("submitted_at", "DATETIME"),
        ],
    },
    "analytics": {
        "customer_churn_monthly": [
            ("month", "TEXT NOT NULL"), ("segment", "TEXT"), ("region", "TEXT"),
            ("active_customers", "INTEGER"), ("churned_customers", "INTEGER"),
            ("churn_rate", "REAL"),
        ],
        "daily_sales_summary": [
            ("sales_date", "DATE NOT NULL"), ("channel", "TEXT"), ("orders", "INTEGER"),
            ("revenue", "REAL"), ("avg_order_value", "REAL"),
        ],
    },
}

ROW_COUNTS = {"categories": 12, "revenue_monthly": 48, "customer_churn_monthly": 72,
              "daily_sales_summary": 180}
DEFAULT_ROWS = 150

POOLS = {
    "city": ["Lucknow", "Gorakhpur", "Pune", "Bengaluru", "Delhi", "Stockholm", "Berlin"],
    "country": ["IN", "SE", "DE", "US"],
    "segment": ["consumer", "smb", "enterprise"],
    "region": ["north", "south", "east", "west"],
    "channel": ["web", "app", "store", "email", "phone"],
    "currency": ["INR", "SEK", "EUR", "USD"],
    "method": ["card", "upi", "bank_transfer", "wallet"],
    "priority": ["low", "medium", "high", "urgent"],
    "category": ["billing", "delivery", "product", "account"],
    "department": ["engineering", "sales", "finance", "support", "people"],
    "job_title": ["engineer", "analyst", "manager", "specialist"],
    "parent_category": ["electronics", "home", "fashion", None],
    "landing_page": ["/", "/pricing", "/blog", "/product"],
    "event_type": ["sent", "open", "click", "bounce"],
    "loyalty_tier": ["bronze", "silver", "gold"],
}
STATUS = {
    "orders": ["placed", "shipped", "delivered", "cancelled", "returned"],
    "invoices": ["open", "paid", "overdue"],
    "tickets": ["open", "pending", "resolved", "closed"],
}
FIRST = ["Asha", "Ravi", "Meera", "Arjun", "Sara", "Erik", "Lena", "Kabir"]
LAST = ["Sharma", "Verma", "Gupta", "Lind", "Berg", "Singh", "Mehta"]
START = date(2025, 1, 1)


def _value(table: str, col: str, ctype: str, i: int, rng: random.Random):
    base = ctype.split()[0]
    if "PRIMARY KEY" in ctype:
        return i
    if col.endswith("_id"):
        return rng.randint(1, DEFAULT_ROWS) if col != "visitor_id" else f"v-{rng.randint(1, 900):04d}"
    if col == "status":
        return rng.choice(STATUS.get(table, ["active", "inactive"]))
    if col in POOLS:
        return rng.choice(POOLS[col])
    if col in ("month", "pay_period"):
        return f"{2025 + (i // 12) % 2}-{i % 12 + 1:02d}"
    if col in ("is_active", "converted"):
        return int(rng.random() < 0.7)
    if col == "full_name":
        return f"{rng.choice(FIRST)} {rng.choice(LAST)}"
    if "email" in col:
        return f"user{i}@example.com"
    if col == "phone":
        return f"+91-90000{i:05d}"
    if col == "bank_account_last4":
        return f"{rng.randint(0, 9999):04d}"
    if base in ("DATE", "DATETIME"):
        d = START + timedelta(days=rng.randint(0, 540))
        return d.isoformat() if base == "DATE" else f"{d.isoformat()} {rng.randint(0, 23):02d}:00:00"
    if col.endswith(("_rate", "_pct")):
        return round(rng.uniform(0, 0.3), 4)
    if col == "score":
        return rng.randint(1, 5)
    if base == "INTEGER":
        return rng.randint(0, 500)
    if base == "REAL":
        return round(rng.uniform(5, 5000), 2)
    if col.endswith("_name"):
        return f"{col.removesuffix('_name').title()} {i}"
    return f"{col} {i}"


def _create_table(con: sqlite3.Connection, table: str, columns: list[tuple[str, str]],
                  rows: int, rng: random.Random) -> None:
    ddl = ", ".join(f"{c} {t}" for c, t in columns)
    con.execute(f"CREATE TABLE {table} ({ddl})")
    data = []
    for i in range(1, rows + 1):
        row = []
        for c, t in columns:
            v = _value(table, c, t, i, rng)
            # ~4% nulls in nullable, non-key columns so profiling has something to show
            if "NOT NULL" not in t and "PRIMARY KEY" not in t and rng.random() < 0.04:
                v = None
            row.append(v)
        data.append(row)
    placeholders = ", ".join("?" for _ in columns)
    con.executemany(f"INSERT INTO {table} VALUES ({placeholders})", data)


def generate_warehouse(warehouse_dir: Path, seed: int = 42, force: bool = False) -> list[Path]:
    """Create one SQLite database per schema. Returns the created files."""
    warehouse_dir.mkdir(parents=True, exist_ok=True)
    existing = list(warehouse_dir.glob("*.db"))
    if existing and not force:
        log.info("Warehouse already exists at %s (use --force to regenerate)", warehouse_dir)
        return sorted(existing)
    for f in existing:
        f.unlink()
    rng = random.Random(seed)
    files = []
    for schema, tables in WAREHOUSE.items():
        path = warehouse_dir / f"{schema}.db"
        with sqlite3.connect(path) as con:
            for table, columns in tables.items():
                _create_table(con, table, columns, ROW_COUNTS.get(table, DEFAULT_ROWS), rng)
        con.close()
        files.append(path)
    log.info("Generated %d schemas in %s", len(files), warehouse_dir)
    return files


def _has_column(con: sqlite3.Connection, table: str, column: str) -> bool:
    return any(r[1] == column for r in con.execute(f"PRAGMA table_info({table})"))


def simulate_schema_change(warehouse_dir: Path, seed: int = 7) -> list[str]:
    """Apply a realistic upstream schema change. Safe to call repeatedly.

    - sales.customers gets a new column ``loyalty_tier``
    - a new table marketing.email_events appears (with an undocumented e-mail column)
    - support.csat_responses is dropped
    """
    rng = random.Random(seed)
    applied: list[str] = []
    with sqlite3.connect(warehouse_dir / "sales.db") as con:
        if not _has_column(con, "customers", "loyalty_tier"):
            con.execute("ALTER TABLE customers ADD COLUMN loyalty_tier TEXT")
            con.execute("UPDATE customers SET loyalty_tier = CASE customer_id % 3 "
                        "WHEN 0 THEN 'gold' WHEN 1 THEN 'silver' ELSE 'bronze' END")
            applied.append("added column sales.customers.loyalty_tier")
    con.close()
    with sqlite3.connect(warehouse_dir / "marketing.db") as con:
        exists = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='email_events'").fetchone()
        if not exists:
            _create_table(con, "email_events", [
                ("event_id", "INTEGER PRIMARY KEY"), ("campaign_id", "INTEGER"),
                ("recipient_email", "TEXT"), ("event_type", "TEXT"), ("event_at", "DATETIME"),
            ], 120, rng)
            applied.append("created table marketing.email_events")
    con.close()
    with sqlite3.connect(warehouse_dir / "support.db") as con:
        exists = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='csat_responses'").fetchone()
        if exists:
            con.execute("DROP TABLE csat_responses")
            applied.append("dropped table support.csat_responses")
    con.close()
    return applied
