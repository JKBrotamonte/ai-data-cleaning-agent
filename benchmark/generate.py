"""Make the benchmark: 5 clean synthetic datasets, then inject known errors into each.

    python -m benchmark.generate          # writes benchmark/jobs/<name>/ (seeded, so always the same)

Each job gets:
  input/<name>.csv   the dirty file the agent cleans
  request.md         what to clean, in plain words
  truth.csv          the clean file (never shown to the agent)
  errors.csv         one row per injected error: row id, column, kind, clean value, dirty value

The data is made up (names, numbers and companies are random), so there is nothing private in it,
and the right answer for every cell is known.
"""

import csv
import random
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).parent
JOBS = HERE / "jobs"
SEED = 2026
ROWS = 300

FIRST = ["James", "Maria", "Wei", "Aisha", "Carlos", "Priya", "John", "Fatima", "Liam", "Sofia", "Noah", "Mei",
         "Omar", "Grace", "Ethan", "Lucia", "Kenji", "Amara", "Diego", "Hannah", "Ravi", "Chloe", "Mateo", "Zara"]
LAST = ["Smith", "Garcia", "Chen", "Khan", "Lopez", "Patel", "Johnson", "Ali", "Brown", "Rossi", "Kim", "Nguyen",
        "Okafor", "Miller", "Silva", "Tanaka", "Davis", "Moreno", "Clark", "Singh", "Walker", "Hughes"]
AREA = ["201", "212", "312", "415", "512", "617", "757", "804", "919", "305"]
STATES = ["Virginia", "Texas", "California", "New York", "Florida", "Illinois", "Arizona", "Georgia"]
DOMAINS = ["example.com", "mail.example.org", "corp.example.net"]


def rnd_date(r, start=date(2022, 1, 1), days=1000):
    return (start + timedelta(days=r.randrange(days))).isoformat()


def rnd_phone(r):
    return f"{r.choice(AREA)}-555-{r.randrange(100, 200):04d}"


def person(r):
    f, l = r.choice(FIRST), r.choice(LAST)
    return f, l, f"{f}.{l}{r.randrange(10, 99)}@{r.choice(DOMAINS)}".lower()


def money(r, lo, hi):
    return f"{r.uniform(lo, hi):.2f}"


# ---------- clean datasets: (columns, rows, column types, request text) ----------

def customers(r):
    cols = ["customer_id", "first_name", "last_name", "email", "phone", "signup_date", "state", "plan"]
    rows = []
    for i in range(ROWS):
        f, l, e = person(r)
        rows.append([str(10001 + i), f, l, e, rnd_phone(r), rnd_date(r), r.choice(STATES),
                     r.choice(["Basic", "Standard", "Premium"]) if r.random() > 0.2 else ""])
    types = {"email": "email", "phone": "phone", "signup_date": "date", "state": "category", "plan": "category"}
    return cols, rows, types


def orders(r):
    cols = ["order_id", "customer_id", "order_date", "product_category", "quantity", "amount", "status"]
    rows = [[str(500001 + i), str(10001 + r.randrange(ROWS)), rnd_date(r),
             r.choice(["Electronics", "Furniture", "Groceries", "Clothing", "Outdoors"]),
             str(r.randrange(1, 9)), money(r, 5, 900),
             r.choice(["Shipped", "Delivered", "Processing", "Cancelled"]) if r.random() > 0.2 else ""]
            for i in range(ROWS)]
    types = {"order_date": "date", "product_category": "category", "quantity": "number", "amount": "number",
             "status": "category"}
    return cols, rows, types


def employees(r):
    cols = ["employee_id", "full_name", "department", "hire_date", "salary", "work_phone", "office"]
    rows = []
    for i in range(ROWS):
        f, l, _ = person(r)
        rows.append([f"E{3001 + i}", f"{f} {l}", r.choice(["Engineering", "Finance", "Operations", "Marketing",
                                                             "Logistics"]),
                     rnd_date(r, date(2015, 1, 1), 3500), money(r, 42000, 160000), rnd_phone(r),
                     r.choice(["Norfolk", "Richmond", "Austin", "Denver"]) if r.random() > 0.2 else ""])
    types = {"department": "category", "hire_date": "date", "salary": "number", "work_phone": "phone",
             "office": "category"}
    return cols, rows, types


def registrations(r):
    cols = ["registration_id", "attendee_email", "event_date", "ticket_type", "price", "country"]
    rows = []
    for i in range(ROWS):
        _, _, e = person(r)
        rows.append([f"R-{70001 + i}", e, rnd_date(r, date(2025, 1, 1), 500),
                     r.choice(["General", "Student", "VIP", "Speaker"]), money(r, 0, 650),
                     r.choice(["United States", "Canada", "Mexico", "Germany", "Japan"]) if r.random() > 0.2 else ""])
    types = {"attendee_email": "email", "event_date": "date", "ticket_type": "category", "price": "number",
             "country": "category"}
    return cols, rows, types


def inventory(r):
    cols = ["sku", "product_name", "category", "supplier", "last_restock_date", "unit_cost", "stock"]
    nouns = ["Widget", "Bracket", "Valve", "Cable", "Filter", "Gasket", "Sensor", "Hinge"]
    rows = [[f"SKU-{9001 + i}", f"{r.choice(nouns)} {r.choice('ABCDEFG')}{r.randrange(10, 99)}",
             r.choice(["Hardware", "Electrical", "Plumbing", "Safety"]),
             r.choice(["Acme Supply", "Northwind Parts", "Globex Industrial", "Initech Wholesale"]),
             rnd_date(r, date(2024, 1, 1), 600), money(r, 0.5, 250), str(r.randrange(0, 500))]
            for i in range(ROWS)]
    types = {"category": "category", "supplier": "category", "last_restock_date": "date", "unit_cost": "number",
             "stock": "number"}
    return cols, rows, types


DATASETS = {"customers": customers, "orders": orders, "employees": employees,
            "registrations": registrations, "inventory": inventory}

REQUEST = """ai_data_sharing: pii_masked
proceed: yes

## Requirements
Clean this file and keep every column with its current name.
- Remove stray spaces at the start, end, or doubled inside values.
- Treat markers like N/A, n/a, null, none and - as empty cells.
- Remove completely empty rows and duplicate rows.
- Write every date as YYYY-MM-DD. Slash dates like 03/04/2024 are month/day/year.
- Write every phone number as 999-999-9999 (all numbers are US numbers).
- Write emails in lowercase.
- Write money and amounts as plain numbers with no currency sign or thousands separator (1234.5, not $1,234.50).
- Fix misspelled and inconsistently capitalized category values so each category is written one way,
  using the most common spelling.

## Answers
(none)
"""


# ---------- error injection ----------

def dirty_date(r, iso):
    d = date.fromisoformat(iso)
    return r.choice([d.strftime("%m/%d/%Y"), d.strftime("%B %d, %Y"), d.strftime("%Y/%m/%d"),
                     d.strftime("%d %b %Y"), f"{d.strftime('%b')} {d.day}, {d.year}"])


def dirty_phone(r, p):
    a, b, c = p.split("-")
    return r.choice([f"({a}) {b}-{c}", f"{a}.{b}.{c}", f"+1 {a} {b} {c}", f"{a}{b}{c}", f"1-{a}-{b}-{c}"])


def dirty_number(r, v):
    n = float(v)
    return r.choice([f"${n:,.2f}", f"{n:,.2f}", f"USD {n:.2f}", f"$ {n:.2f}"]) if n >= 1000 or r.random() < 0.6 \
        else f"${n:.2f}"


def typo(r, v):
    if len(v) >= 6 and r.random() < 0.6:
        i = r.randrange(1, len(v) - 2)
        return v[:i] + v[i + 1] + v[i] + v[i + 2:] if r.random() < 0.5 else v[:i] + v[i] + v[i:]
    return r.choice([v.lower(), v.upper()])


def inject(r, cols, rows, types):
    """Returns (dirty rows, errors). About a third of rows get one cell error each; then duplicates
    and empty rows are added."""
    errors, dirty = [], [list(row) for row in rows]
    id_col = cols[0]
    text_cols = [c for c in cols[1:] if types.get(c) in (None, "category")]
    for i in r.sample(range(len(dirty)), k=len(dirty) // 3):
        row = dirty[i]
        options = []
        for c in cols[1:]:
            v, t = row[cols.index(c)], types.get(c)
            if v == "":
                options.append((c, "missing_marker"))
            elif t == "date":
                options.append((c, "date_format"))
            elif t == "phone":
                options.append((c, "phone_format"))
            elif t == "number" and "." in v:
                options.append((c, "number_format"))
            elif t == "email":
                options.append((c, "email_case"))
            elif t == "category":
                options.append((c, "category_typo"))
        options += [(c, "whitespace") for c in text_cols if row[cols.index(c)]]
        by_kind = {}
        for c, k in options:
            by_kind.setdefault(k, []).append(c)
        kind = r.choice(sorted(by_kind))  # pick the kind first so every kind gets a fair share
        col = r.choice(by_kind[kind])
        j = cols.index(col)
        clean = row[j]
        bad = {"missing_marker": lambda: r.choice(["N/A", "n/a", "null", "-", "none", "NA"]),
               "date_format": lambda: dirty_date(r, clean),
               "phone_format": lambda: dirty_phone(r, clean),
               "number_format": lambda: dirty_number(r, clean),
               "email_case": lambda: r.choice([clean.title(), clean.upper(), " " + clean + " "]),
               "category_typo": lambda: typo(r, clean),
               "whitespace": lambda: r.choice([" " + clean, clean + "  ", clean.replace(" ", "  ") if " " in clean
                                               else clean + " "])}[kind]()
        if bad == clean:
            continue
        row[j] = bad
        errors.append({"id": row[0], "column": col, "kind": kind, "clean": clean, "dirty": bad})

    for i in r.sample(range(len(dirty)), k=8):  # duplicates of the dirty rows, some differing only by spaces
        copy = list(dirty[i])
        if r.random() < 0.4 and text_cols:
            j = cols.index(r.choice(text_cols))
            if copy[j]:
                copy[j] = copy[j] + " "
        dirty.insert(r.randrange(len(dirty) + 1), copy)
        errors.append({"id": copy[0], "column": "", "kind": "duplicate_row", "clean": "", "dirty": ""})
    for _ in range(3):
        dirty.insert(r.randrange(len(dirty) + 1), [""] * len(cols))
        errors.append({"id": "", "column": "", "kind": "empty_row", "clean": "", "dirty": ""})
    return dirty, errors


def write_csv(path: Path, header, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def main():
    r = random.Random(SEED)
    for name, make in DATASETS.items():
        cols, rows, types = make(r)
        dirty, errors = inject(r, cols, rows, types)
        job = JOBS / name
        write_csv(job / "truth.csv", cols, rows)
        write_csv(job / "input" / f"{name}.csv", cols, dirty)
        write_csv(job / "errors.csv", ["id", "column", "kind", "clean", "dirty"],
                  [[e[k] for k in ("id", "column", "kind", "clean", "dirty")] for e in errors])
        (job / "types.csv").write_text("column,type\n" + "".join(f"{c},{t}\n" for c, t in types.items()),
                                       encoding="utf-8")
        (job / "request.md").write_text(REQUEST, encoding="utf-8")
        print(f"{name}: {len(rows)} clean rows, {len(dirty)} dirty rows, {len(errors)} injected errors")


if __name__ == "__main__":
    main()
