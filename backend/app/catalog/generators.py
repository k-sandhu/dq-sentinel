"""Deterministic, seeded generators for the built-in data catalog.

Each ``generate_*`` builds ONE backing database (all tables an entry needs, even
ones only referenced by cross-table custom-SQL checks) and returns
``{table_name: row_count}``. Generators are stdlib-only for SQLite and use the
``duckdb`` driver (a backend dependency) for the analytics warehouse entry.

Every generator plants the *characteristic data-quality issues* an analyst would
actually find in that domain — NULLs, malformed values, domain/casing
violations, magnitude outliers, cross-column mismatches, broken referential
integrity, and a freshness gap near the dataset's SLA — so that once the catalog
dataset is connected, profiling and the curated checks have real signal to find.
Style mirrors ``data/generate_sample_data.py``.

The connection is opened writable by the seeding engine and closed before the
read-only connector profiles the file (DuckDB allows a single writer).
"""

from __future__ import annotations

import random
import sqlite3
from datetime import datetime, timedelta
from typing import Any

# Shared vocabularies ---------------------------------------------------------

FIRST = ["James", "Mary", "Wei", "Aisha", "Carlos", "Yuki", "Emma", "Raj", "Olga", "Tunde",
         "Liam", "Sofia", "Noah", "Ava", "Mateo", "Zara", "Ethan", "Priya", "Lucas", "Mia",
         "Hannah", "Omar", "Lena", "Diego", "Nina", "Sam", "Grace", "Ibrahim", "Chloe", "Yusuf"]
LAST = ["Smith", "Garcia", "Chen", "Patel", "Kim", "Mueller", "Rossi", "Tanaka", "Okafor",
        "Johnson", "Lee", "Brown", "Singh", "Lopez", "Ivanov", "Kowalski", "Nakamura", "Khan",
        "Nguyen", "Andersson", "Costa", "Haddad", "Petrov", "Reyes", "Olsen", "Murphy"]
COUNTRIES = ["US", "CA", "GB", "DE", "FR", "IN", "JP", "AU", "BR", "NG"]
CITIES = ["New York", "London", "Berlin", "Toronto", "Mumbai", "Tokyo", "Sydney", "Paris",
          "Chicago", "Lagos", "Sao Paulo", "Amsterdam"]


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _date(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d")


def _name(rng: random.Random) -> str:
    return f"{rng.choice(FIRST)} {rng.choice(LAST)}"


# 1. Retail — Commerce DB (sqlite) -------------------------------------------

_RETAIL_SCHEMA = """
CREATE TABLE customers (
    customer_id   INTEGER PRIMARY KEY,
    email         TEXT,
    full_name     TEXT NOT NULL,
    country       TEXT,
    signup_date   TEXT,
    loyalty_tier  TEXT,
    marketing_opt_in INTEGER
);
CREATE TABLE products (
    product_id    INTEGER PRIMARY KEY,
    sku           TEXT,
    product_name  TEXT NOT NULL,
    category      TEXT,
    unit_price    REAL,
    active        INTEGER
);
CREATE TABLE orders (
    order_id      INTEGER PRIMARY KEY,
    customer_id   INTEGER,
    order_date    TEXT,
    status        TEXT,
    currency      TEXT,
    total_amount  REAL,
    ship_country  TEXT
);
CREATE TABLE order_items (
    order_item_id INTEGER PRIMARY KEY,
    order_id      INTEGER,
    product_id    INTEGER,
    quantity      INTEGER,
    unit_price    REAL,
    line_total    REAL
);
CREATE TABLE payments (
    payment_id    INTEGER PRIMARY KEY,
    order_id      INTEGER,
    paid_at       TEXT,
    method        TEXT,
    amount        REAL,
    status        TEXT
);
"""

_CATEGORIES = ["Electronics", "Home & Kitchen", "Sports", "Books", "Toys", "Beauty", "Garden"]
_ADJ = ["Pro", "Ultra", "Classic", "Eco", "Smart", "Mini", "Max", "Prime", "Lite", "Plus"]
_NOUNS = ["Blender", "Headphones", "Lamp", "Backpack", "Kettle", "Monitor", "Chair", "Notebook",
          "Speaker", "Bottle", "Tracker", "Camera", "Router", "Mug", "Desk", "Keyboard"]
_ORDER_STATUS = ["pending", "paid", "shipped", "delivered", "cancelled"]
_PAY_METHODS = ["card", "paypal", "bank_transfer", "gift_card"]


def generate_retail(con: sqlite3.Connection, rng: random.Random) -> dict[str, int]:
    con.executescript(_RETAIL_SCHEMA)
    now = datetime.now()
    n_customers, n_products, n_orders, span = 2_000, 300, 8_000, 540

    customers, used_emails = [], []
    for cid in range(1, n_customers + 1):
        name = _name(rng)
        email: str | None = f"{name.lower().replace(' ', '.')}.{cid}@example.com"
        r = rng.random()
        if r < 0.02:
            email = None
        elif r < 0.03:
            email = rng.choice(["not-an-email", f"user{cid}@", f"user{cid}example.com", "a@b"])
        elif r < 0.035 and used_emails:
            email = rng.choice(used_emails)
        if email:
            used_emails.append(email)
        country = rng.choice(COUNTRIES)
        if rng.random() < 0.015:
            country = rng.choice([country.lower(), f"{country} "])
        signup = now - timedelta(days=rng.uniform(1, span), hours=rng.uniform(0, 24))
        if rng.random() < 0.002:
            signup = now + timedelta(days=rng.uniform(5, 60))
        customers.append((
            cid, email, name, country, _date(signup),
            rng.choices(["bronze", "silver", "gold", "platinum"], weights=[55, 28, 13, 4])[0],
            int(rng.random() < 0.6),
        ))
    con.executemany("INSERT INTO customers VALUES (?,?,?,?,?,?,?)", customers)

    products = []
    for pid in range(1, n_products + 1):
        category = rng.choice(_CATEGORIES)
        if rng.random() < 0.01:
            category = rng.choice(["electronics", "Electronics ", "Home and Kitchen"])
        price = round(rng.uniform(4, 240), 2)
        r = rng.random()
        if r < 0.008:
            price = round(-price, 2)
        elif r < 0.012:
            price = 0.0
        elif r < 0.017:
            price = round(price * 100, 2)  # magnitude typo
        products.append((pid, f"SKU-{pid:05d}", f"{rng.choice(_ADJ)} {rng.choice(_NOUNS)} {pid}",
                         category, price, int(rng.random() < 0.93)))
    con.executemany("INSERT INTO products VALUES (?,?,?,?,?,?)", products)
    price_by_pid = {p[0]: p[4] for p in products}

    orders, items, payments = [], [], []
    item_id = payment_id = 0
    for oid in range(1, n_orders + 1):
        age_days = rng.betavariate(1.1, 2.6) * span
        odate = now - timedelta(days=age_days, hours=28)  # newest ~28h old -> fails 24h SLA
        if rng.random() < 0.0008:
            odate = now + timedelta(days=rng.uniform(10, 400))
        status = rng.choices(_ORDER_STATUS, weights=[8, 22, 25, 40, 5])[0]
        if rng.random() < 0.004:
            status = rng.choice(["PAID", "Shipped", "unknown", "refunded?"])
        customer_id = rng.randint(1, n_customers)
        order_total = 0.0
        for _ in range(rng.choices([1, 2, 3, 4, 5], weights=[42, 30, 16, 8, 4])[0]):
            item_id += 1
            pid = rng.randint(1, n_products)
            qty = rng.choices([1, 2, 3, 4], weights=[68, 20, 8, 4])[0]
            if rng.random() < 0.006:
                qty = rng.choice([0, -1, -2])
            unit = abs(price_by_pid[pid]) or 9.99
            line = round(qty * unit, 2)
            if rng.random() < 0.003:
                line = round(line + rng.uniform(5, 80), 2)
            target = oid
            if rng.random() < 0.01:
                target = n_orders + rng.randint(1000, 99999)  # orphan
            items.append((item_id, target, pid, qty, unit, line))
            if target == oid:
                order_total += line
        total = round(order_total, 2)
        r = rng.random()
        if r < 0.015:
            total = round(total * rng.uniform(0.6, 1.6) + rng.uniform(1, 40), 2)  # disagrees
        elif r < 0.02:
            total = round(-abs(total), 2)
        orders.append((oid, customer_id, _iso(odate), status, "USD", total, rng.choice(COUNTRIES)))
        if status in ("paid", "shipped", "delivered") and rng.random() >= 0.012:
            payment_id += 1
            amount = total if rng.random() >= 0.002 else round(total * 100, 2)
            payments.append((payment_id, oid, _iso(odate + timedelta(hours=rng.uniform(0, 48))),
                             rng.choice(_PAY_METHODS), amount, "settled"))
    con.executemany("INSERT INTO orders VALUES (?,?,?,?,?,?,?)", orders)
    con.executemany("INSERT INTO order_items VALUES (?,?,?,?,?,?)", items)
    con.executemany("INSERT INTO payments VALUES (?,?,?,?,?,?)", payments)
    con.commit()
    return {"customers": len(customers), "products": len(products), "orders": len(orders),
            "order_items": len(items), "payments": len(payments)}


# 2. Finance — Payments Ledger (sqlite) --------------------------------------

_LEDGER_SCHEMA = """
CREATE TABLE accounts (
    account_id    INTEGER PRIMARY KEY,
    account_name  TEXT,
    account_type  TEXT,
    opened_at     TEXT,
    status        TEXT
);
CREATE TABLE transactions (
    txn_id        INTEGER PRIMARY KEY,
    account_id    INTEGER,
    posted_at     TEXT,
    value_date    TEXT,
    amount        REAL,
    currency      TEXT,
    txn_type      TEXT,
    status        TEXT,
    counterparty  TEXT,
    mcc           TEXT
);
"""

_TXN_TYPES = ["debit", "credit", "fee", "refund", "transfer"]
_CURRENCIES = ["USD", "EUR", "GBP", "JPY", "CAD"]
_TXN_STATUS = ["settled", "pending", "reversed", "failed"]
_MCCS = ["5411", "5812", "5732", "4900", "6011", "7372", "5999"]


def generate_payments_ledger(con: sqlite3.Connection, rng: random.Random) -> dict[str, int]:
    con.executescript(_LEDGER_SCHEMA)
    now = datetime.now()
    n_accounts, n_txn = 800, 12_000

    accounts = []
    for aid in range(1, n_accounts + 1):
        accounts.append((aid, f"{rng.choice(['ACME', 'Globex', 'Initech', 'Umbrella', 'Stark'])} "
                         f"{rng.choice(['Operating', 'Payroll', 'Reserve', 'Escrow'])} {aid}",
                         rng.choice(["checking", "savings", "merchant", "suspense"]),
                         _date(now - timedelta(days=rng.uniform(60, 2000))),
                         rng.choices(["active", "dormant", "closed"], weights=[85, 10, 5])[0]))
    con.executemany("INSERT INTO accounts VALUES (?,?,?,?,?)", accounts)

    txns = []
    for tid in range(1, n_txn + 1):
        posted = now - timedelta(days=rng.betavariate(1.1, 3.0) * 365, hours=rng.uniform(7, 10))
        value = posted + timedelta(days=rng.choice([0, 0, 0, 1, 2]))
        if rng.random() < 0.004:
            value = posted - timedelta(days=rng.randint(1, 4))  # value-dated before posting
        amount = round(rng.uniform(5, 4000), 2)
        ttype = rng.choice(_TXN_TYPES)
        if ttype in ("debit", "fee"):
            amount = -amount
        status = rng.choices(_TXN_STATUS, weights=[80, 12, 4, 4])[0]
        r = rng.random()
        if r < 0.0025:
            amount = round(amount * 100, 2)  # magnitude outlier
        elif r < 0.004 and status == "settled":
            amount = 0.0  # settled with no value
        currency = rng.choice(_CURRENCIES)
        if rng.random() < 0.012:
            currency = rng.choice(["usd", "Eur", "us dollar", ""])  # casing/domain noise
        counterparty: str | None = f"{rng.choice(['INV', 'PO', 'REF'])}-{rng.randint(10000, 99999)}"
        if rng.random() < 0.02:
            counterparty = None
        account_id = rng.randint(1, n_accounts)
        if rng.random() < 0.006:
            account_id = n_accounts + rng.randint(1, 500)  # orphan account ref
        txns.append((tid, account_id, _iso(posted), _date(value), amount, currency, ttype,
                     status, counterparty, rng.choice(_MCCS)))
    con.executemany("INSERT INTO transactions VALUES (?,?,?,?,?,?,?,?,?,?)", txns)
    con.commit()
    return {"accounts": len(accounts), "transactions": len(txns)}


# 3. People — HRIS employee directory (sqlite, PII) --------------------------

_HR_SCHEMA = """
CREATE TABLE employees (
    employee_id       INTEGER PRIMARY KEY,
    full_name         TEXT NOT NULL,
    work_email        TEXT,
    personal_email    TEXT,
    ssn               TEXT,
    phone             TEXT,
    dob               TEXT,
    hire_date         TEXT,
    department        TEXT,
    title             TEXT,
    manager_id        INTEGER,
    employment_status TEXT,
    salary            REAL,
    location          TEXT,
    updated_at        TEXT
);
"""

_DEPARTMENTS = ["Engineering", "Sales", "Marketing", "Finance", "People", "Operations",
                "Customer Success", "Legal", "Product"]
_TITLES = ["Analyst", "Manager", "Director", "Engineer", "Specialist", "Lead", "Coordinator", "VP"]
_EMP_STATUS = ["active", "on_leave", "terminated", "contractor"]


def generate_employees(con: sqlite3.Connection, rng: random.Random) -> dict[str, int]:
    con.executescript(_HR_SCHEMA)
    now = datetime.now()
    n = 1_200
    rows = []
    for eid in range(1, n + 1):
        name = _name(rng)
        slug = name.lower().replace(" ", ".")
        work: str | None = f"{slug}.{eid}@corp.example.com"
        r = rng.random()
        if r < 0.015:
            work = None
        elif r < 0.03:
            work = rng.choice([f"{slug}@corp", f"{slug}.corp.example.com", "n/a"])  # malformed
        personal = f"{slug}{eid}@gmail.com" if rng.random() > 0.05 else None
        ssn = f"{rng.randint(100, 899):03d}-{rng.randint(10, 99):02d}-{rng.randint(1000, 9999):04d}"
        phone = f"+1-{rng.randint(200, 989)}-{rng.randint(200, 989)}-{rng.randint(1000, 9999)}"
        dob = _date(now - timedelta(days=rng.uniform(22 * 365, 63 * 365)))
        hire = now - timedelta(days=rng.uniform(10, 12 * 365))
        if rng.random() < 0.003:
            hire = now + timedelta(days=rng.uniform(10, 120))  # future hire date
        dept = rng.choice(_DEPARTMENTS)
        if rng.random() < 0.01:
            dept = rng.choice(["engineering", "SALES", "Mktg", "people ops"])  # casing/variant
        manager = rng.randint(1, n) if rng.random() > 0.08 else None
        if manager and rng.random() < 0.01:
            manager = n + rng.randint(1, 300)  # orphan manager
        status = rng.choices(_EMP_STATUS, weights=[78, 6, 10, 6])[0]
        salary = round(rng.uniform(45_000, 210_000), 2)
        rr = rng.random()
        if rr < 0.004:
            salary = round(-salary, 2)  # negative salary
        elif rr < 0.007:
            salary = round(salary * 10, 2)  # magnitude outlier
        loc = rng.choice(CITIES)
        updated = now - timedelta(hours=rng.uniform(1, 26))  # newest ~1-26h old
        rows.append((eid, name, work, personal, ssn, phone, dob, _date(hire), dept,
                     rng.choice(_TITLES), manager, status, salary, loc, _iso(updated)))
    con.executemany("INSERT INTO employees VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    con.commit()
    return {"employees": len(rows)}


# 4. Marketing — Web Analytics clickstream (duckdb) --------------------------

_EVENT_TYPES = ["page_view", "click", "add_to_cart", "checkout", "purchase", "signup", "search"]
_DEVICES = ["desktop", "mobile", "tablet"]
_BROWSERS = ["Chrome", "Safari", "Firefox", "Edge"]
_UTM_SOURCES = ["google", "facebook", "newsletter", "direct", "twitter", "affiliate"]
_UTM_MEDIUMS = ["cpc", "organic", "email", "social", "referral"]


def generate_web_events(con: Any, rng: random.Random) -> dict[str, int]:
    """DuckDB analytics warehouse. ``con`` is a writable duckdb connection."""
    con.execute(
        """CREATE TABLE web_events (
            event_id    BIGINT,
            event_ts    VARCHAR,
            session_id  VARCHAR,
            user_id     VARCHAR,
            event_type  VARCHAR,
            page_url    VARCHAR,
            referrer    VARCHAR,
            utm_source  VARCHAR,
            utm_medium  VARCHAR,
            device      VARCHAR,
            browser     VARCHAR,
            country     VARCHAR,
            duration_ms BIGINT,
            revenue     DOUBLE
        )"""
    )
    now = datetime.now()
    n = 30_000
    rows = []
    for eid in range(1, n + 1):
        ts = now - timedelta(minutes=rng.uniform(90, 90 * 24 * 60))  # last ~90 days, newest ~90m
        if rng.random() < 0.0005:
            ts = now + timedelta(days=rng.uniform(1, 30))  # future event
        session = f"sess-{rng.randint(1, n // 6):07d}"
        user: str | None = f"u-{rng.randint(1, n // 4):07d}" if rng.random() > 0.22 else None
        etype = rng.choices(_EVENT_TYPES, weights=[45, 25, 10, 7, 5, 4, 4])[0]
        if rng.random() < 0.006:
            etype = rng.choice(["PageView", "click ", "unknown", ""])  # domain/casing noise
        page = f"https://shop.example.com/{rng.choice(['', 'p/', 'c/', 'search', 'cart'])}{rng.randint(1, 500)}"
        if rng.random() < 0.004:
            page = rng.choice(["/relative/path", "not a url", ""])  # malformed url
        revenue = round(rng.uniform(5, 320), 2) if etype == "purchase" else 0.0
        if revenue and rng.random() < 0.01:
            revenue = round(-revenue, 2)  # negative revenue
        duration = rng.randint(50, 600_000)
        if rng.random() < 0.005:
            duration = rng.choice([0, -1, -250])  # invalid duration
        session_val = None if rng.random() < 0.003 else session  # a few null sessions
        rows.append((eid, _iso(ts), session_val, user, etype, page,
                     rng.choice(["", "https://google.com", "https://t.co/x"]),
                     rng.choice(_UTM_SOURCES), rng.choice(_UTM_MEDIUMS), rng.choice(_DEVICES),
                     rng.choice(_BROWSERS), rng.choice(COUNTRIES), duration, revenue))
    # DuckDB row-by-row executemany is pathologically slow (~100s for 30k); insert
    # via a registered DataFrame instead (DuckDB's fast columnar path, sub-second).
    import pandas as pd

    df = pd.DataFrame(rows, columns=[
        "event_id", "event_ts", "session_id", "user_id", "event_type", "page_url",
        "referrer", "utm_source", "utm_medium", "device", "browser", "country",
        "duration_ms", "revenue",
    ])
    con.register("incoming_web_events", df)
    con.execute("INSERT INTO web_events SELECT * FROM incoming_web_events")
    con.unregister("incoming_web_events")
    return {"web_events": len(rows)}


# 5. Supply Chain — Logistics shipments (sqlite) -----------------------------

_SHIP_SCHEMA = """
CREATE TABLE shipments (
    shipment_id   INTEGER PRIMARY KEY,
    order_ref     TEXT,
    carrier       TEXT,
    service_level TEXT,
    origin        TEXT,
    destination   TEXT,
    shipped_at    TEXT,
    delivered_at  TEXT,
    status        TEXT,
    weight_kg     REAL,
    cost          REAL,
    updated_at    TEXT
);
"""

_CARRIERS = ["UPS", "FedEx", "DHL", "USPS", "Maersk", "DBSchenker"]
_SERVICE = ["ground", "express", "overnight", "freight", "economy"]
_SHIP_STATUS = ["created", "in_transit", "out_for_delivery", "delivered", "returned", "lost"]


def generate_shipments(con: sqlite3.Connection, rng: random.Random) -> dict[str, int]:
    con.executescript(_SHIP_SCHEMA)
    now = datetime.now()
    n = 9_000
    rows = []
    for sid in range(1, n + 1):
        shipped = now - timedelta(days=rng.betavariate(1.2, 2.5) * 180, hours=rng.uniform(13, 16))
        status = rng.choices(_SHIP_STATUS, weights=[10, 28, 12, 42, 5, 3])[0]
        delivered: str | None = None
        if status == "delivered":
            d = shipped + timedelta(days=rng.uniform(0.5, 9))
            if rng.random() < 0.01:
                d = shipped - timedelta(days=rng.uniform(0.2, 2))  # delivered before shipped
            delivered = _iso(d)
        carrier: str | None = rng.choice(_CARRIERS)
        if rng.random() < 0.012:
            carrier = rng.choice([None, "ups", "Fed Ex", "unknown"])
        if rng.random() < 0.004:
            status = rng.choice(["DELIVERED", "Lost?", "transit"])  # domain/casing noise
        weight = round(rng.uniform(0.2, 80), 2)
        if rng.random() < 0.006:
            weight = round(-weight, 2)  # negative weight
        cost = round(rng.uniform(5, 900), 2)
        if rng.random() < 0.004:
            cost = round(cost * 100, 2)  # cost outlier
        updated = now - timedelta(hours=rng.uniform(1, 20))
        rows.append((sid, f"ORD-{rng.randint(1, 8000):06d}", carrier, rng.choice(_SERVICE),
                     rng.choice(CITIES), rng.choice(CITIES), _iso(shipped), delivered, status,
                     weight, cost, _iso(updated)))
    con.executemany("INSERT INTO shipments VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    con.commit()
    return {"shipments": len(rows)}


# 6. Healthcare — EHR clinical encounters (sqlite, PII) ----------------------

_EHR_SCHEMA = """
CREATE TABLE encounters (
    encounter_id   INTEGER PRIMARY KEY,
    patient_id     INTEGER,
    patient_name   TEXT,
    mrn            TEXT,
    dob            TEXT,
    ssn            TEXT,
    address        TEXT,
    admit_at       TEXT,
    discharge_at   TEXT,
    department     TEXT,
    encounter_type TEXT,
    provider_id    INTEGER,
    diagnosis_code TEXT,
    status         TEXT,
    length_of_stay REAL,
    billed_amount  REAL,
    updated_at     TEXT
);
"""

_ENC_DEPTS = ["Cardiology", "Oncology", "Emergency", "Pediatrics", "Orthopedics", "Neurology",
              "Radiology", "General Medicine"]
_ENC_TYPES = ["inpatient", "outpatient", "emergency", "observation", "telehealth"]
_ENC_STATUS = ["scheduled", "in_progress", "discharged", "cancelled", "no_show"]
_ICD = ["E11.9", "I10", "J45.909", "M54.5", "K21.9", "N39.0", "R07.9", "F41.1", "Z00.00"]


def generate_clinical_encounters(con: sqlite3.Connection, rng: random.Random) -> dict[str, int]:
    con.executescript(_EHR_SCHEMA)
    now = datetime.now()
    n = 7_000
    rows = []
    for enc in range(1, n + 1):
        admit = now - timedelta(days=rng.betavariate(1.1, 2.4) * 365, hours=rng.uniform(20, 24))
        etype = rng.choice(_ENC_TYPES)
        status = rng.choices(_ENC_STATUS, weights=[15, 12, 60, 8, 5])[0]
        discharge: str | None = None
        los: float | None = None
        if status == "discharged":
            hours = rng.uniform(2, 14 * 24)
            d = admit + timedelta(hours=hours)
            if rng.random() < 0.008:
                d = admit - timedelta(hours=rng.uniform(2, 48))  # discharge before admit
            discharge = _iso(d)
            los = round(hours / 24.0, 2)
            if rng.random() < 0.005:
                los = round(-los, 2)  # negative LOS
        mrn: str | None = f"MRN{rng.randint(100000, 999999)}"
        if rng.random() < 0.01:
            mrn = rng.choice([None, "mrn-123", "00000", "N/A"])  # malformed MRN
        if rng.random() < 0.005:
            etype = rng.choice(["Inpatient", "ER", "unknown"])  # domain/casing noise
        patient_id: int | None = rng.randint(1, n // 2)
        if rng.random() < 0.004:
            patient_id = None  # missing patient link
        diag = rng.choice(_ICD)
        if rng.random() < 0.008:
            diag = rng.choice(["", "XYZ", "999"])  # invalid code
        billed = round(rng.uniform(120, 48_000), 2)
        if rng.random() < 0.004:
            billed = round(-billed, 2)  # negative billed amount
        updated = now - timedelta(hours=rng.uniform(1, 22))
        rows.append((enc, patient_id, _name(rng), mrn,
                     _date(now - timedelta(days=rng.uniform(365, 95 * 365))),
                     f"{rng.randint(100, 899):03d}-{rng.randint(10, 99):02d}-{rng.randint(1000, 9999):04d}",
                     f"{rng.randint(1, 9999)} {rng.choice(['Oak', 'Main', 'Elm', 'Pine'])} St",
                     _iso(admit), discharge, rng.choice(_ENC_DEPTS), etype,
                     rng.randint(1, 400), diag, status, los, billed, _iso(updated)))
    con.executemany(
        "INSERT INTO encounters VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows
    )
    con.commit()
    return {"encounters": len(rows)}


# 7. Product — SaaS subscriptions (sqlite) -----------------------------------

_SUB_SCHEMA = """
CREATE TABLE subscriptions (
    subscription_id INTEGER PRIMARY KEY,
    account_id      INTEGER,
    plan            TEXT,
    status          TEXT,
    seats           INTEGER,
    mrr             REAL,
    started_at      TEXT,
    canceled_at     TEXT,
    renews_at       TEXT,
    event_date      TEXT,
    updated_at      TEXT
);
"""

_PLANS = ["free", "starter", "pro", "business", "enterprise"]
_SUB_STATUS = ["trialing", "active", "past_due", "canceled", "paused"]


def generate_product_subscriptions(con: sqlite3.Connection, rng: random.Random) -> dict[str, int]:
    con.executescript(_SUB_SCHEMA)
    now = datetime.now()
    n = 4_000
    rows = []
    for subid in range(1, n + 1):
        started = now - timedelta(days=rng.uniform(5, 3 * 365), hours=rng.uniform(0, 24))
        status = rng.choices(_SUB_STATUS, weights=[12, 60, 8, 16, 4])[0]
        canceled: str | None = None
        if status == "canceled":
            c = started + timedelta(days=rng.uniform(15, 600))
            if rng.random() < 0.01:
                c = started - timedelta(days=rng.uniform(1, 30))  # canceled before started
            canceled = _iso(c)
        plan = rng.choices(_PLANS, weights=[18, 30, 28, 18, 6])[0]
        if rng.random() < 0.008:
            plan = rng.choice(["Pro", "PREMIUM", "tier-2"])  # domain/casing noise
        seats = rng.choices([1, 2, 3, 5, 10, 25, 50], weights=[30, 20, 15, 15, 10, 6, 4])[0]
        if rng.random() < 0.006:
            seats = rng.choice([0, -1, -5])  # invalid seats
        mrr = round({"free": 0, "starter": 29, "pro": 99, "business": 299,
                     "enterprise": 1200}.get(plan, 99) * rng.uniform(0.8, 1.2), 2)
        if rng.random() < 0.004:
            mrr = round(-mrr, 2)  # negative MRR
        renews = _iso(now + timedelta(days=rng.uniform(1, 365))) if status in ("active", "trialing") else None
        event = now - timedelta(hours=rng.uniform(1, 26))
        rows.append((subid, rng.randint(1, n), plan, status, seats, mrr, _iso(started),
                     canceled, renews, _date(event), _iso(event)))
    con.executemany("INSERT INTO subscriptions VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
    con.commit()
    return {"subscriptions": len(rows)}
