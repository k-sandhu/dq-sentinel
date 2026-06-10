"""Generate samples/shopdb.sqlite — a synthetic e-commerce database with
deterministic, documented data-quality issues for demoing DQ Sentinel.

Stdlib-only. Usage:
    python data/generate_sample_data.py [--force] [--out samples/shopdb.sqlite]

Seeded issues (also written to samples/ISSUES.md):
  customers:   NULL emails, malformed emails, duplicate emails, inconsistent
               country casing, future signup dates
  products:    negative/zero prices, 100x price typos (ML outlier bait),
               category typo variants
  orders:      totals that disagree with line items, negative totals, far-future
               order dates, status casing/domain violations, freshness gap
               (newest order is ~30h old)
  order_items: orphan order_ids, zero/negative quantities, line_total mismatches
  payments:    100x amount typos, missing payments for delivered orders
"""

import argparse
import random
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

SEED = 42
N_CUSTOMERS = 5_000
N_PRODUCTS = 400
N_ORDERS = 30_000
DAYS_SPAN = 540
FRESHNESS_GAP_HOURS = 30  # newest order is this old -> 24h freshness SLA fails

FIRST = ["James", "Mary", "Wei", "Aisha", "Carlos", "Yuki", "Emma", "Raj", "Olga", "Tunde",
         "Liam", "Sofia", "Noah", "Ava", "Mateo", "Zara", "Ethan", "Priya", "Lucas", "Mia"]
LAST = ["Smith", "Garcia", "Chen", "Patel", "Kim", "Mueller", "Rossi", "Tanaka", "Okafor",
        "Johnson", "Lee", "Brown", "Singh", "Lopez", "Ivanov", "Kowalski", "Nakamura", "Khan"]
COUNTRIES = ["US", "CA", "GB", "DE", "FR", "IN", "JP", "AU", "BR", "NG"]
CATEGORIES = ["Electronics", "Home & Kitchen", "Sports", "Books", "Toys", "Beauty", "Garden"]
ADJ = ["Pro", "Ultra", "Classic", "Eco", "Smart", "Mini", "Max", "Prime", "Lite", "Plus"]
NOUNS = ["Blender", "Headphones", "Lamp", "Backpack", "Kettle", "Monitor", "Chair", "Notebook",
         "Speaker", "Bottle", "Tracker", "Camera", "Router", "Mug", "Desk", "Keyboard"]
STATUSES = ["pending", "paid", "shipped", "delivered", "cancelled"]
METHODS = ["card", "paypal", "bank_transfer", "gift_card"]

SCHEMA = """
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
CREATE VIEW order_revenue AS
    SELECT o.order_id, o.order_date, o.status, o.total_amount,
           SUM(i.line_total) AS items_total, COUNT(i.order_item_id) AS item_count
    FROM orders o LEFT JOIN order_items i ON i.order_id = o.order_id
    GROUP BY o.order_id;
"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(Path(__file__).resolve().parent.parent / "samples" / "shopdb.sqlite"))
    parser.add_argument("--force", action="store_true", help="overwrite an existing file")
    args = parser.parse_args()

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        if not args.force:
            raise SystemExit(f"{out} already exists. Use --force to regenerate.")
        out.unlink()

    rng = random.Random(SEED)
    now = datetime.now()
    issues: dict[str, int] = {}

    def bump(key: str, n: int = 1) -> None:
        issues[key] = issues.get(key, 0) + n

    con = sqlite3.connect(out)
    con.executescript(SCHEMA)

    # ---------------- customers ----------------
    customers = []
    used_emails: list[str] = []
    for cid in range(1, N_CUSTOMERS + 1):
        name = f"{rng.choice(FIRST)} {rng.choice(LAST)}"
        email = f"{name.lower().replace(' ', '.')}.{cid}@example.com"
        r = rng.random()
        if r < 0.02:
            email = None
            bump("customers.email NULL")
        elif r < 0.03:
            email = rng.choice(["not-an-email", f"user{cid}@", f"user{cid}example.com", "a@b"])
            bump("customers.email malformed")
        elif r < 0.035 and used_emails:
            email = rng.choice(used_emails)
            bump("customers.email duplicate")
        if email:
            used_emails.append(email)

        country = rng.choice(COUNTRIES)
        if rng.random() < 0.015:
            country = rng.choice([country.lower(), country.title(), f"{country} "])
            bump("customers.country inconsistent casing/space")

        signup = now - timedelta(days=rng.uniform(1, DAYS_SPAN), hours=rng.uniform(0, 24))
        if rng.random() < 0.002:
            signup = now + timedelta(days=rng.uniform(5, 60))
            bump("customers.signup_date in the future")

        customers.append((
            cid, email, name, country, signup.strftime("%Y-%m-%d"),
            rng.choices(["bronze", "silver", "gold", "platinum"], weights=[55, 28, 13, 4])[0],
            rng.random() < 0.6,
        ))
    con.executemany("INSERT INTO customers VALUES (?,?,?,?,?,?,?)", customers)

    # ---------------- products ----------------
    products = []
    for pid in range(1, N_PRODUCTS + 1):
        category = rng.choice(CATEGORIES)
        if rng.random() < 0.01:
            category = rng.choice(["electronics", "Electronics ", "Home and Kitchen"])
            bump("products.category typo/variant")
        price = round(rng.uniform(4, 240), 2)
        r = rng.random()
        if r < 0.008:
            price = round(-price, 2)
            bump("products.unit_price negative")
        elif r < 0.012:
            price = 0.0
            bump("products.unit_price zero")
        elif r < 0.017:
            price = round(price * 100, 2)  # classic magnitude typo
            bump("products.unit_price 100x outlier")
        products.append((
            pid, f"SKU-{pid:05d}", f"{rng.choice(ADJ)} {rng.choice(NOUNS)} {pid}",
            category, price, rng.random() < 0.93,
        ))
    con.executemany("INSERT INTO products VALUES (?,?,?,?,?,?)", products)
    price_by_pid = {p[0]: p[4] for p in products}

    # ---------------- orders + items + payments ----------------
    orders, items, payments = [], [], []
    item_id = payment_id = 0
    for oid in range(1, N_ORDERS + 1):
        # recency-weighted order dates, newest ~FRESHNESS_GAP_HOURS old
        age_days = rng.betavariate(1.1, 2.6) * DAYS_SPAN
        odate = now - timedelta(days=age_days, hours=FRESHNESS_GAP_HOURS)
        if rng.random() < 0.0008:
            odate = now + timedelta(days=rng.uniform(10, 400))
            bump("orders.order_date far future")

        status = rng.choices(STATUSES, weights=[8, 22, 25, 40, 5])[0]
        if rng.random() < 0.004:
            status = rng.choice(["PAID", "Shipped", "unknown", "refunded?"])
            bump("orders.status domain/casing violation")

        customer_id = rng.randint(1, N_CUSTOMERS)
        n_items = rng.choices([1, 2, 3, 4, 5], weights=[42, 30, 16, 8, 4])[0]
        order_total = 0.0
        for _ in range(n_items):
            item_id += 1
            pid = rng.randint(1, N_PRODUCTS)
            qty = rng.choices([1, 2, 3, 4], weights=[68, 20, 8, 4])[0]
            if rng.random() < 0.006:
                qty = rng.choice([0, -1, -2])
                bump("order_items.quantity non-positive")
            unit = abs(price_by_pid[pid]) or 9.99
            line = round(qty * unit, 2)
            if rng.random() < 0.003:
                line = round(line + rng.uniform(5, 80), 2)
                bump("order_items.line_total mismatch")
            target_order = oid
            if rng.random() < 0.01:
                target_order = N_ORDERS + rng.randint(1000, 99999)  # orphan
                bump("order_items.order_id orphan")
            items.append((item_id, target_order, pid, qty, unit, line))
            if target_order == oid:
                order_total += line

        total = round(order_total, 2)
        r = rng.random()
        if r < 0.015:
            total = round(total * rng.uniform(0.6, 1.6) + rng.uniform(1, 40), 2)
            bump("orders.total_amount disagrees with items")
        elif r < 0.02:
            total = round(-abs(total), 2)
            bump("orders.total_amount negative")

        orders.append((
            oid, customer_id, odate.strftime("%Y-%m-%d %H:%M:%S"), status, "USD", total,
            rng.choice(COUNTRIES),
        ))

        if status in ("paid", "shipped", "delivered"):
            if rng.random() < 0.012:
                bump("payments missing for paid/delivered order")
            else:
                payment_id += 1
                amount = total
                if rng.random() < 0.002:
                    amount = round(amount * 100, 2)
                    bump("payments.amount 100x outlier")
                payments.append((
                    payment_id, oid,
                    (odate + timedelta(hours=rng.uniform(0, 48))).strftime("%Y-%m-%d %H:%M:%S"),
                    rng.choice(METHODS), amount, "settled",
                ))

    con.executemany("INSERT INTO orders VALUES (?,?,?,?,?,?,?)", orders)
    con.executemany("INSERT INTO order_items VALUES (?,?,?,?,?,?)", items)
    con.executemany("INSERT INTO payments VALUES (?,?,?,?,?,?)", payments)
    con.commit()

    counts = {
        t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        for t in ("customers", "products", "orders", "order_items", "payments")
    }
    con.close()

    issues_md = out.parent / "ISSUES.md"
    with open(issues_md, "w", encoding="utf-8") as f:
        f.write("# Seeded data-quality issues in shopdb.sqlite\n\n")
        f.write(f"Generated {now:%Y-%m-%d %H:%M} with seed {SEED}. Row counts: {counts}\n\n")
        f.write(f"Freshness: newest order is ~{FRESHNESS_GAP_HOURS}h old "
                "(fails a 24h SLA, passes 48h).\n\n| Issue | Count |\n|---|---|\n")
        for k in sorted(issues):
            f.write(f"| {k} | {issues[k]} |\n")

    print(f"Wrote {out}")
    for t, n in counts.items():
        print(f"  {t}: {n} rows")
    print(f"Seeded issue summary -> {issues_md}")
    for k in sorted(issues):
        print(f"  {issues[k]:>5}  {k}")
    print("\nConnect with DSN:")
    print(f"  sqlite:///{out.as_posix()}")


if __name__ == "__main__":
    main()
