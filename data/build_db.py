"""Build the sample online-store database (data/store.db). The data is made up and the build is deterministic.

    python data/build_db.py
"""
import os
import random
import sqlite3
from datetime import date, timedelta

PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "store.db")

SCHEMA = """
CREATE TABLE customers (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    email TEXT NOT NULL,          -- private, the service never reads it
    city TEXT NOT NULL,
    country TEXT NOT NULL,
    signup_date TEXT NOT NULL     -- YYYY-MM-DD
);
CREATE TABLE products (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    price REAL NOT NULL           -- current list price in USD
);
CREATE TABLE orders (
    id INTEGER PRIMARY KEY,
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    order_date TEXT NOT NULL,     -- YYYY-MM-DD
    status TEXT NOT NULL          -- delivered, shipped, cancelled or returned
);
CREATE TABLE order_items (
    id INTEGER PRIMARY KEY,
    order_id INTEGER NOT NULL REFERENCES orders(id),
    product_id INTEGER NOT NULL REFERENCES products(id),
    quantity INTEGER NOT NULL,
    unit_price REAL NOT NULL      -- price paid per unit in USD
);
"""

FIRST = ["Aarav", "Maya", "Liam", "Sofia", "Noah", "Zara", "Ethan", "Priya", "Lucas", "Amara", "Oliver", "Chen", "Mateo",
         "Leila", "Jack", "Ines", "Omar", "Hana", "Diego", "Nina", "Arjun", "Elena", "Kofi", "Yuki", "Sam", "Ruth"]
LAST = ["Patel", "Garcia", "Smith", "Kim", "Nguyen", "Silva", "Brown", "Khan", "Rossi", "Lee", "Costa", "Ivanov", "Sato",
        "Ahmed", "Muller", "Jones", "Okafor", "Singh", "Lopez", "Wang"]
PLACES = [("Mumbai", "India"), ("Delhi", "India"), ("Austin", "USA"), ("Boston", "USA"), ("Seattle", "USA"),
          ("London", "UK"), ("Manchester", "UK"), ("Berlin", "Germany"), ("Munich", "Germany"), ("Paris", "France"),
          ("Toronto", "Canada"), ("Sydney", "Australia"), ("Madrid", "Spain"), ("Tokyo", "Japan")]
CATALOG = {
    "Electronics": ["Wireless Mouse", "USB-C Hub", "Bluetooth Speaker", "Webcam", "Mechanical Keyboard", "Power Bank",
                    "Noise Cancelling Headphones", "Smart Watch", "Phone Stand", "Monitor Light"],
    "Home": ["Desk Lamp", "Coffee Grinder", "Throw Blanket", "Wall Clock", "Plant Pot", "Kettle", "Cutting Board",
             "Storage Box", "Scented Candle", "Door Mat"],
    "Books": ["Python Basics", "Data Stories", "Cooking at Home", "Space Atlas", "Mindful Mornings", "City Walks",
              "The Quiet Garden", "Money Notes", "Design Habits", "Night Trains"],
    "Sports": ["Yoga Mat", "Water Bottle", "Resistance Bands", "Running Belt", "Jump Rope", "Gym Bag", "Foam Roller",
               "Cycling Gloves", "Tennis Balls", "Skipping Timer"],
}
STATUSES = ["delivered"] * 14 + ["shipped"] * 3 + ["cancelled"] * 2 + ["returned"]


def build(path=PATH, seed=42):
    rng = random.Random(seed)
    if os.path.exists(path):
        os.remove(path)
    con = sqlite3.connect(path)
    con.executescript(SCHEMA)
    start = date(2023, 1, 1)
    customers = []
    for i in range(1, 201):
        name = f"{rng.choice(FIRST)} {rng.choice(LAST)}"
        city, country = rng.choice(PLACES)
        customers.append((i, name, f"user{i}@example.com", city, country, (start + timedelta(days=rng.randrange(0, 700))).isoformat()))
    products = []
    pid = 0
    for category, names in CATALOG.items():
        for name in names:
            pid += 1
            products.append((pid, name, category, round(rng.uniform(6, 180), 2)))
    con.executemany("INSERT INTO customers VALUES (?,?,?,?,?,?)", customers)
    con.executemany("INSERT INTO products VALUES (?,?,?,?)", products)
    item_id = 0
    for oid in range(1, 1501):
        customer = rng.choice(customers)
        signup = date.fromisoformat(customer[5])
        when = signup + timedelta(days=rng.randrange(0, 730 - (signup - start).days + 1))
        con.execute("INSERT INTO orders VALUES (?,?,?,?)", (oid, customer[0], when.isoformat(), rng.choice(STATUSES)))
        for p in rng.sample(products, rng.randint(1, 4)):
            item_id += 1
            con.execute("INSERT INTO order_items VALUES (?,?,?,?,?)", (item_id, oid, p[0], rng.randint(1, 5), p[3]))
    con.commit()
    con.close()


if __name__ == "__main__":
    build()
    con = sqlite3.connect(PATH)
    for t in ("customers", "products", "orders", "order_items"):
        print(t, con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0])
